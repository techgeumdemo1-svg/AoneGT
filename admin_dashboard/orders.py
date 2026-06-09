from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional, Tuple

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from shop.models import Order, OrderReturn
from shop.serializers import (
    ORDER_CUSTOMER_TRACKING_STAGE_LABELS,
    OrderSerializer,
    _effective_customer_tracking_stage,
    order_code_for_order,
)
from shop.services.account_credit import get_user_credit_balance, record_prepaid_payment_success
from shop.services.order_tracking import (
    cancelled_at,
    record_tracking_stage,
    tracking_stage_events,
    TRACKING_HISTORY_CANCELLED_KEY,
)
from shop.services.order_email import handle_customer_tracking_stage_change
from shop.services.order_status_notifications import notify_order_tracking_status_change
from shop.services.zoho_books_payment import (
    is_prepaid_at_checkout_payment_method,
    staff_record_zoho_books_payment_for_order,
)

from .activity_log_utils import record_admin_activity
from .models import AdminActivityLog
from .views import IsStaffUser

# Admin-facing labels (request) → customer_tracking_stage key or "cancelled"
_ADMIN_STATUS_ALIASES = {
    "pending": "pending",
    "confirmed": "packed",
    "packed": "packed",
    "under processing": "packed",
    "under_processing": "packed",
    "out for delivery": "out_for_delivery",
    "out_for_delivery": "out_for_delivery",
    "delivered": "delivered",
    "returned": "returned",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "cancel": "cancelled",
}
for _key, _label in ORDER_CUSTOMER_TRACKING_STAGE_LABELS.items():
    _ADMIN_STATUS_ALIASES[_label.lower()] = _key
    _ADMIN_STATUS_ALIASES[_key] = _key


def _admin_orders_queryset():
    return (
        Order.objects.select_related("user", "store")
        .prefetch_related("items", "returns__lines__order_item")
    )


def _normalize_admin_status(raw: str) -> Optional[str]:
    key = (raw or "").strip().lower()
    return _ADMIN_STATUS_ALIASES.get(key)


def _display_status_for_order(order: Order) -> str:
    return OrderSerializer(order).data.get("display_status", "Pending")


def _reload_admin_order(pk: int) -> Order:
    return get_object_or_404(_admin_orders_queryset(), pk=pk)


def _admin_order_payload(order: Order) -> dict:
    data = OrderSerializer(order).data
    data["customer"] = {
        "id": order.user_id,
        "email": order.user.email,
        "first_name": order.user.first_name,
        "last_name": order.user.last_name,
        "phone": order.user.phone,
    }
    data["store_name"] = order.store.name
    return data


def _optional_decimal(raw) -> Optional[Decimal]:
    if raw is None or str(raw).strip() == "":
        return None
    return Decimal(str(raw)).quantize(Decimal("0.01"))


class AdminOrderListSerializer(serializers.ModelSerializer):
    order_id = serializers.IntegerField(source="id", read_only=True)
    order_code = serializers.SerializerMethodField()
    customer_id = serializers.IntegerField(source="user_id", read_only=True)
    customer_email = serializers.EmailField(source="user.email", read_only=True)
    customer_name = serializers.SerializerMethodField()
    store_id = serializers.IntegerField(read_only=True)
    store_name = serializers.CharField(source="store.name", read_only=True)
    display_status = serializers.SerializerMethodField()
    items_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            "order_id",
            "order_code",
            "customer_id",
            "customer_email",
            "customer_name",
            "store_id",
            "store_name",
            "status",
            "customer_tracking_stage",
            "display_status",
            "payment_method",
            "payment_status",
            "currency",
            "total",
            "items_count",
            "created_at",
            "updated_at",
        )

    def get_order_code(self, obj):
        return order_code_for_order(obj)

    def get_customer_name(self, obj):
        parts = [obj.user.first_name or "", obj.user.last_name or ""]
        return " ".join(p for p in parts if p).strip() or obj.user.email

    def get_display_status(self, obj):
        return _display_status_for_order(obj)

    def get_items_count(self, obj):
        return int(sum((int(it.quantity or 0) for it in obj.items.all()), 0))


class AdminOrderStatusUpdateSerializer(serializers.Serializer):
    status = serializers.CharField()

    def validate_status(self, value):
        normalized = _normalize_admin_status(value)
        if not normalized:
            allowed = sorted(
                {label for label in ORDER_CUSTOMER_TRACKING_STAGE_LABELS.values()}
                | {"Cancelled"}
            )
            raise serializers.ValidationError(
                f"Invalid status. Allowed values: {', '.join(allowed)}."
            )
        return normalized


def _paginate_queryset(queryset, request):
    try:
        page = max(int(request.query_params.get("page", 1)), 1)
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(max(int(request.query_params.get("page_size", 20)), 1), 100)
    except (TypeError, ValueError):
        page_size = 20
    total = queryset.count()
    start = (page - 1) * page_size
    end = start + page_size
    return queryset[start:end], {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
    }


def _parse_order_list_date(raw: str) -> Optional[date]:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _apply_order_date_filter(queryset, request) -> Tuple[object, Optional[str], Optional[dict]]:
    """
    Date filters (order created_at, local timezone).

    Presets via period:
      - omit / all / all_time → no date filter
      - 7days / last_7_days → last 7 calendar days including today
      - this_month / month → first day of current month through today

    Custom range (overrides period when either date is set):
      - date_from or start_date (inclusive)
      - date_to or end_date (inclusive)
    """
    date_from_raw = (
        request.query_params.get("date_from")
        or request.query_params.get("start_date")
        or ""
    ).strip()
    date_to_raw = (
        request.query_params.get("date_to")
        or request.query_params.get("end_date")
        or ""
    ).strip()
    period = (request.query_params.get("period") or "").strip().lower()

    if date_from_raw or date_to_raw:
        date_from = _parse_order_list_date(date_from_raw)
        date_to = _parse_order_list_date(date_to_raw)
        if date_from_raw and date_from is None:
            return queryset, "Invalid date_from/start_date. Use YYYY-MM-DD.", None
        if date_to_raw and date_to is None:
            return queryset, "Invalid date_to/end_date. Use YYYY-MM-DD.", None
        if date_from and date_to and date_from > date_to:
            return queryset, "date_from must be on or before date_to.", None
        meta = {"type": "custom", "date_from": None, "date_to": None}
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
            meta["date_from"] = date_from.isoformat()
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)
            meta["date_to"] = date_to.isoformat()
        return queryset, None, meta

    if not period or period in ("all", "all_time", "alltime"):
        return queryset, None, {"type": "all"}

    today = timezone.localdate()

    if period in ("7days", "last_7_days", "last7days"):
        start = today - timedelta(days=6)
        return (
            queryset.filter(created_at__date__gte=start, created_at__date__lte=today),
            None,
            {"type": "last_7_days", "date_from": start.isoformat(), "date_to": today.isoformat()},
        )

    if period in ("this_month", "month", "thismonth"):
        start = today.replace(day=1)
        return (
            queryset.filter(created_at__date__gte=start, created_at__date__lte=today),
            None,
            {"type": "this_month", "date_from": start.isoformat(), "date_to": today.isoformat()},
        )

    return (
        queryset,
        "Invalid period. Use: all, 7days, this_month, or custom date_from/date_to (YYYY-MM-DD).",
        None,
    )


def _apply_order_list_filters(queryset, request):
    status_filter = (request.query_params.get("status") or "").strip()
    if status_filter:
        normalized = _normalize_admin_status(status_filter)
        if normalized == "cancelled":
            queryset = queryset.filter(status=Order.Status.CANCELLED)
        elif normalized:
            queryset = queryset.filter(
                status=Order.Status.SYNCED,
                customer_tracking_stage=normalized,
            )
        else:
            queryset = queryset.filter(status=status_filter)

    search = (request.query_params.get("search") or "").strip()
    if search:
        if search.isdigit():
            queryset = queryset.filter(pk=int(search))
        else:
            queryset = queryset.filter(user__email__icontains=search)

    store_id = (request.query_params.get("store_id") or "").strip()
    if store_id.isdigit():
        queryset = queryset.filter(store_id=int(store_id))

    queryset, date_err, date_meta = _apply_order_date_filter(queryset, request)
    return queryset, date_err, date_meta


def _apply_status_update(order: Order, new_status: str) -> Order:
    previous_stage = order.customer_tracking_stage
    was_cancelled = order.status == Order.Status.CANCELLED

    if new_status == "cancelled":
        record_tracking_stage(order, TRACKING_HISTORY_CANCELLED_KEY, save=False)
        order.status = Order.Status.CANCELLED
        order.save(update_fields=["status", "tracking_stage_history", "updated_at"])
        if not was_cancelled:
            notify_order_tracking_status_change(
                order,
                stage_key="cancelled",
                previous_stage=previous_stage,
            )
        return order

    update_fields = ["customer_tracking_stage", "tracking_stage_history", "updated_at"]
    order.customer_tracking_stage = new_status
    record_tracking_stage(order, new_status, save=False)

    if order.status in (Order.Status.PENDING_ZOHO_SYNC, Order.Status.SYNC_FAILED):
        order.status = Order.Status.SYNCED
        update_fields.append("status")

    order.save(update_fields=update_fields)
    handle_customer_tracking_stage_change(order, previous_stage)
    notify_order_tracking_status_change(
        order,
        stage_key=new_status,
        previous_stage=previous_stage,
    )
    return order


def _build_order_timeline(order: Order) -> list:
    events = []

    def add_event(*, key, label, at, extra=None):
        if not at:
            return
        at_value = at.isoformat() if hasattr(at, "isoformat") else str(at)
        payload = {
            "key": key,
            "label": label,
            "at": at_value,
        }
        if extra:
            payload.update(extra)
        events.append(payload)

    add_event(key="order_placed", label="Order placed", at=order.created_at)

    if order.status == Order.Status.CANCELLED:
        add_event(
            key="cancelled",
            label="Cancelled",
            at=cancelled_at(order) or order.updated_at,
            extra={"status": order.status},
        )
    else:
        add_event(key="zoho_synced", label="Synced to Zoho", at=order.zoho_synced_at)
        add_event(
            key="sales_order_created",
            label="Sales order created",
            at=order.zoho_books_salesordered_at,
        )
        add_event(
            key="invoice_created",
            label="Invoice created",
            at=order.zoho_books_invoiced_at,
        )
        add_event(
            key="payment_recorded",
            label="Payment recorded",
            at=order.zoho_books_paid_at,
        )
        add_event(
            key="out_for_delivery_email",
            label="Out for delivery email sent",
            at=order.out_for_delivery_email_sent_at,
        )

    for stage_event in tracking_stage_events(order):
        if stage_event["key"] == TRACKING_HISTORY_CANCELLED_KEY:
            continue
        add_event(
            key=f"tracking_{stage_event['key']}",
            label=stage_event["label"],
            at=stage_event["at"],
            extra={"customer_tracking_stage": stage_event["key"]},
        )

    for ret in order.returns.all().order_by("created_at"):
        add_event(
            key=f"return_{ret.pk}",
            label=f"Return requested ({ret.get_status_display()})",
            at=ret.created_at,
            extra={"return_id": ret.pk, "return_status": ret.status},
        )

    events.sort(key=lambda row: row["at"])
    return events


class AdminOrderListAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        qs, date_err, date_filter = _apply_order_list_filters(
            _admin_orders_queryset().order_by("-created_at"),
            request,
        )
        if date_err:
            return Response({"detail": date_err}, status=status.HTTP_400_BAD_REQUEST)

        page_qs, pagination = _paginate_queryset(qs, request)
        payload = {
            **pagination,
            "results": AdminOrderListSerializer(page_qs, many=True).data,
        }
        if date_filter:
            payload["date_filter"] = date_filter
        return Response(payload, status=status.HTTP_200_OK)


class AdminOrderDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request, pk):
        order = get_object_or_404(_admin_orders_queryset(), pk=pk)
        data = OrderSerializer(order).data
        data["customer"] = {
            "id": order.user_id,
            "email": order.user.email,
            "first_name": order.user.first_name,
            "last_name": order.user.last_name,
            "phone": order.user.phone,
        }
        data["store_name"] = order.store.name
        return Response(data, status=status.HTTP_200_OK)


class AdminOrderStatusUpdateAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def patch(self, request, pk):
        order = get_object_or_404(_admin_orders_queryset(), pk=pk)
        serializer = AdminOrderStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = _apply_status_update(order, serializer.validated_data["status"])
        new_status = serializer.validated_data["status"]
        record_admin_activity(
            request,
            category=AdminActivityLog.Category.ORDERS,
            action="order.status_updated",
            message=f"Updated order #{order.pk} status to {new_status}.",
            target_type="order",
            target_id=order.pk,
            metadata={"status": new_status},
        )
        return Response(
            {
                "message": "Order status updated.",
                "order_id": order.pk,
                "status": ORDER_CUSTOMER_TRACKING_STAGE_LABELS.get(
                    serializer.validated_data["status"],
                    "Cancelled" if serializer.validated_data["status"] == "cancelled" else serializer.validated_data["status"],
                ),
                "display_status": _display_status_for_order(order),
                "order_status": order.status,
                "customer_tracking_stage": order.customer_tracking_stage,
            },
            status=status.HTTP_200_OK,
        )


class AdminOrderTimelineAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request, pk):
        order = get_object_or_404(_admin_orders_queryset(), pk=pk)
        tracking = OrderSerializer(order).data.get("tracking", {})
        return Response(
            {
                "order_id": order.pk,
                "order_code": order_code_for_order(order),
                "display_status": _display_status_for_order(order),
                "tracking": tracking,
                "timeline": _build_order_timeline(order),
            },
            status=status.HTTP_200_OK,
        )


class AdminOrderInvoiceAPIView(APIView):
    """Create Zoho Books invoice for the order (requires sales order in Zoho)."""

    permission_classes = [IsAuthenticated, IsStaffUser]

    def post(self, request, pk):
        get_object_or_404(_admin_orders_queryset(), pk=pk)
        from shop.services.zoho_books_invoice import staff_create_zoho_books_invoice_for_order

        ok, message = staff_create_zoho_books_invoice_for_order(pk)
        order = _reload_admin_order(pk)
        return Response(
            {
                "status": "success" if ok else "error",
                "message": message,
                "order": _admin_order_payload(order),
            },
            status=status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST,
        )


class AdminOrderVerifyPaymentAPIView(APIView):
    """
    Verify / record payment for an order.

    - Prepaid (gateway / pay-by-link): marks payment_status paid and credits user account.
    - If invoice exists: records Zoho Books customer payment against the invoice.
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def post(self, request, pk):
        order = get_object_or_404(_admin_orders_queryset(), pk=pk)
        amount = _optional_decimal(request.data.get("amount"))
        gateway_reference = (request.data.get("gateway_reference") or "").strip()
        payment_method = (request.data.get("payment_method") or "").strip()

        steps = []

        if order.payment_status == Order.PaymentStatus.PENDING:
            if not is_prepaid_at_checkout_payment_method(order.payment_method):
                return Response(
                    {
                        "status": "error",
                        "message": "Only gateway or pay-by-link orders can be verified while payment is pending.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            ok, message, order = record_prepaid_payment_success(
                pk,
                amount=amount,
                gateway_reference=gateway_reference,
            )
            if not ok or order is None:
                return Response(
                    {"status": "error", "message": message},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            steps.append(message)
            order = _reload_admin_order(pk)

        if (order.zoho_books_invoice_id or "").strip() and not (order.zoho_books_payment_id or "").strip():
            ok, message = staff_record_zoho_books_payment_for_order(
                pk,
                amount=amount,
                payment_method=payment_method,
                gateway_reference=gateway_reference,
            )
            if not ok:
                return Response(
                    {
                        "status": "error",
                        "message": message,
                        "steps_completed": steps,
                        "order": _admin_order_payload(_reload_admin_order(pk)),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            steps.append(message)
            order = _reload_admin_order(pk)
        elif not steps:
            if order.payment_status == Order.PaymentStatus.PAID and (order.zoho_books_payment_id or "").strip():
                message = "Payment already verified."
            elif not (order.zoho_books_invoice_id or "").strip():
                message = "No invoice yet. Create invoice first, or verify prepaid payment for pending gateway/paylink orders."
            else:
                message = "Nothing to verify for this order."
            return Response(
                {
                    "status": "success" if order.payment_status == Order.PaymentStatus.PAID else "error",
                    "message": message,
                    "order": _admin_order_payload(order),
                },
                status=status.HTTP_200_OK if order.payment_status == Order.PaymentStatus.PAID else status.HTTP_400_BAD_REQUEST,
            )

        payload = {
            "status": "success",
            "message": " ".join(steps) if steps else "Payment verified.",
            "steps_completed": steps,
            "order": _admin_order_payload(order),
        }
        if is_prepaid_at_checkout_payment_method(order.payment_method):
            payload["credit_balance_aed"] = str(get_user_credit_balance(order.user))
        return Response(payload, status=status.HTTP_200_OK)
