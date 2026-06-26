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
    order_display_status,
)
from shop.services.account_credit import get_user_credit_balance, record_prepaid_payment_success
from shop.services.order_cancel import cancel_order
from shop.services.order_tracking import (
    cancelled_at,
    record_tracking_stage,
    tracking_stage_events,
    TRACKING_HISTORY_CANCELLED_KEY,
)
from shop.services.order_email import handle_customer_tracking_stage_change
from shop.services.order_status_notifications import notify_order_tracking_status_change
from shop.services.card_on_delivery_payment import (
    is_card_on_delivery_order,
    order_ready_for_card_on_delivery_collect,
    submit_card_on_delivery_collection,
)
from shop.services.geidea import GeideaSessionError, create_geidea_session
from shop.services.geidea_reconcile import reconcile_missed_geidea_callback
from shop.services.order_delivery_payment import (
    finalize_cod_delivery_and_payment,
    is_cod_order,
    maybe_auto_mark_delivered_on_payment,
    order_ready_for_cod_collect,
)
from shop.services.zoho_books_invoice import (
    resolve_invoice_detail_for_order,
    staff_create_zoho_books_invoice_for_order,
)
from shop.services.zoho_books_payment import (
    is_pay_on_delivery_payment_method,
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
    return order_display_status(order)


def _status_history_label(stage_key: str) -> str:
    key = (stage_key or "").strip().lower()
    if key == "cancelled":
        return "Cancelled"
    return ORDER_CUSTOMER_TRACKING_STAGE_LABELS.get(
        key,
        key.replace("_", " ").title(),
    )


def _status_history_actor(log: AdminActivityLog) -> dict:
    if log.actor_id:
        user = log.actor
        parts = [user.first_name or "", user.last_name or ""]
        name = " ".join(p for p in parts if p).strip() or user.email
        return {
            "id": log.actor_id,
            "email": log.actor_email or user.email,
            "name": name,
        }
    if log.actor_email:
        return {"id": None, "email": log.actor_email, "name": log.actor_email}
    return {"id": None, "email": "", "name": "System"}


def _build_order_status_history(order: Order) -> list:
    """Who changed each order tracking status and when."""
    history: list[dict] = []
    admin_stage_keys: set[str] = set()

    status_from_action = {
        "order.status_updated": lambda meta: (meta or {}).get("status"),
        "order.cod_collected": lambda meta: Order.CustomerTrackingStage.DELIVERED,
        "order.card_collected": lambda meta: Order.CustomerTrackingStage.DELIVERED,
    }

    logs = (
        AdminActivityLog.objects.filter(
            category=AdminActivityLog.Category.ORDERS,
            target_type="order",
            target_id=order.pk,
            action__in=status_from_action.keys(),
        )
        .select_related("actor")
        .order_by("created_at")
    )
    for log in logs:
        stage_key = status_from_action[log.action](log.metadata)
        if not stage_key:
            continue
        stage_key = str(stage_key).strip().lower()
        admin_stage_keys.add(stage_key)
        history.append({
            "status_key": stage_key,
            "status_label": _status_history_label(stage_key),
            "changed_at": log.created_at.isoformat(),
            "changed_by": _status_history_actor(log),
            "source": "admin",
            "action": log.action,
            "message": log.message,
        })

    for stage_event in tracking_stage_events(order):
        stage_key = stage_event["key"]
        if stage_key in admin_stage_keys:
            continue
        history.append({
            "status_key": stage_key,
            "status_label": stage_event["label"],
            "changed_at": stage_event["at"],
            "changed_by": {"id": None, "email": "", "name": "System"},
            "source": "system",
            "action": "tracking_stage_recorded",
            "message": f"Status set to {stage_event['label']}.",
        })

    history.sort(key=lambda row: row["changed_at"])
    return history


def _reload_admin_order(pk: int) -> Order:
    return get_object_or_404(_admin_orders_queryset(), pk=pk)


def _admin_order_payload(order: Order) -> dict:
    return build_admin_order_detail_payload(order)


def build_admin_order_detail_payload(order: Order) -> dict:
    """Admin order detail: list fields + delivery address, items, tracking."""
    data = AdminOrderListSerializer(order).data
    currency = ((order.currency or '') or 'AED').strip() or 'AED'

    def money(value) -> str:
        return str(Decimal(str(value or 0)).quantize(Decimal('0.01')))

    data['delivery_charge'] = money(order.shipping_amount)
    data['shipping_amount'] = money(order.shipping_amount)
    data['tax_percent'] = money(order.vat_percent)
    data['tax_amount'] = money(order.vat_amount)
    data['delivery_address'] = _order_delivery_address(order)
    items = []
    for it in order.items.all():
        unit = Decimal(str(it.unit_price or 0)).quantize(Decimal('0.01'))
        line = Decimal(str(it.line_total or 0)).quantize(Decimal('0.01'))
        items.append({
            'item_id': it.pk,
            'product_id': it.product_id,
            'product_name': it.product_name,
            'sku': it.sku or '',
            'quantity': int(it.quantity or 0),
            'unit_price': str(unit),
            'unit_price_display': f'{currency} {unit}',
            'line_total': str(line),
            'line_total_display': f'{currency} {line}',
        })
    data['items'] = items
    tracking = OrderSerializer(order).data.get('tracking') or {}
    data['tracking_status'] = {
        'order_status': order.status,
        'customer_tracking_stage': order.customer_tracking_stage or '',
        'display_status': data.get('display_status') or _display_status_for_order(order),
        'tracking': tracking,
    }
    data['customer'] = {
        'id': order.user_id,
        'email': order.user.email,
        'first_name': order.user.first_name,
        'last_name': order.user.last_name,
        'phone': order.user.phone,
    }
    data['store_name'] = order.store.name
    return data


def _optional_decimal(raw) -> Optional[Decimal]:
    if raw is None or str(raw).strip() == "":
        return None
    return Decimal(str(raw)).quantize(Decimal("0.01"))


def _order_delivery_address(order: Order) -> dict:
    return {
        'name': order.shipping_name or '',
        'phone': order.shipping_phone or '',
        'address': order.shipping_address or '',
        'city': order.shipping_city or '',
        'state': order.shipping_state or '',
        'postal_code': order.shipping_postal_code or '',
        'country': order.shipping_country or '',
    }


class AdminOrderListSerializer(serializers.ModelSerializer):
    order_id = serializers.IntegerField(source="id", read_only=True)
    order_code = serializers.SerializerMethodField()
    customer_id = serializers.IntegerField(source="user_id", read_only=True)
    customer_email = serializers.EmailField(source="user.email", read_only=True)
    customer_name = serializers.SerializerMethodField()
    customer = serializers.SerializerMethodField()
    cancelled_at = serializers.SerializerMethodField()
    delivery_address = serializers.SerializerMethodField()
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
            "customer",
            "cancelled_at",
            "delivery_address",
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

    def get_customer(self, obj):
        user = obj.user
        return {
            'id': user.pk,
            'email': user.email,
            'first_name': user.first_name or '',
            'last_name': user.last_name or '',
            'phone': user.phone or '',
        }

    def get_cancelled_at(self, obj):
        if obj.status != Order.Status.CANCELLED:
            return None
        return cancelled_at(obj)

    def get_delivery_address(self, obj):
        return _order_delivery_address(obj)

    def get_display_status(self, obj):
        return _display_status_for_order(obj)

    def get_items_count(self, obj):
        return int(sum((int(it.quantity or 0) for it in obj.items.all()), 0))


class AdminOrderCollectCardSerializer(serializers.Serializer):
    invoice_id = serializers.CharField(required=False, allow_blank=True)
    gateway_reference = serializers.CharField(required=False, allow_blank=True)
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
    )
    transaction_status = serializers.CharField(required=False, allow_blank=True, default='paid')


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


def _parse_order_id_query_param(request, *, required=True):
    order_id = (request.query_params.get('id') or '').strip()
    if not order_id:
        if required:
            return None, Response(
                {'detail': 'Query parameter id is required and must be a positive integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None, None
    if not order_id.isdigit():
        return None, Response(
            {'detail': 'Query parameter id is required and must be a positive integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return int(order_id), None


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


def _apply_order_list_filters(queryset, request, *, forced_status: Optional[str] = None):
    status_filter = (forced_status or request.query_params.get("status") or "").strip()
    applied_status = None
    if status_filter:
        normalized = _normalize_admin_status(status_filter)
        applied_status = normalized or status_filter
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
    return queryset, date_err, date_meta, applied_status


def _admin_orders_list_response(request, *, forced_status: Optional[str] = None):
    qs, date_err, date_filter, applied_status = _apply_order_list_filters(
        _admin_orders_queryset().order_by("-created_at"),
        request,
        forced_status=forced_status,
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
    if applied_status:
        payload["status_filter"] = applied_status
    return Response(payload, status=status.HTTP_200_OK)


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
    """
    GET /api/admin/orders/                    — paginated list
    GET /api/admin/orders/?status=cancelled   — cancelled orders with customer details
    GET /api/admin/orders/?id=<id>            — order detail with delivery, items, tracking
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        if (request.query_params.get('id') or '').strip():
            order_id, err = _parse_order_id_query_param(request)
            if err:
                return err
            order = get_object_or_404(_admin_orders_queryset(), pk=order_id)
            return Response(build_admin_order_detail_payload(order), status=status.HTTP_200_OK)

        return _admin_orders_list_response(request)


class AdminCancelledOrderListAPIView(APIView):
    """
    GET /api/admin/orders/cancelled/ — paginated cancelled orders with customer details.

    Alias for GET /api/admin/orders/?status=cancelled
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        return _admin_orders_list_response(request, forced_status='cancelled')


class AdminOrderDetailAPIView(APIView):
    """GET /api/admin/orders/detail/?id=<order_id> — same payload as /orders/?id="""

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        order_id, err = _parse_order_id_query_param(request)
        if err:
            return err
        order = get_object_or_404(_admin_orders_queryset(), pk=order_id)
        return Response(build_admin_order_detail_payload(order), status=status.HTTP_200_OK)


class AdminOrderStatusUpdateAPIView(APIView):
    """PATCH /api/admin/orders/status/?id=<order_id>"""

    permission_classes = [IsAuthenticated, IsStaffUser]

    def patch(self, request):
        order_id, err = _parse_order_id_query_param(request)
        if err:
            return err
        order = get_object_or_404(_admin_orders_queryset(), pk=order_id)
        serializer = AdminOrderStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]
        if new_status == "delivered":
            if is_cod_order(order):
                return Response(
                    {
                        "detail": (
                            "Cash on delivery orders cannot be marked delivered here. "
                            "Use POST /api/admin/orders/collect-cod/?id=<order_id> after collecting cash."
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if (
                order.payment_method == Order.PaymentMethod.CARD_ON_DELIVERY
                and order.payment_status == Order.PaymentStatus.PENDING
            ):
                return Response(
                    {
                        "detail": (
                            "Card payment is still pending. "
                            "Collect payment via Geidea before marking delivered."
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        if new_status == "cancelled":
            if order.status != Order.Status.CANCELLED:
                ok, message = cancel_order(order.pk, customer=False, notify=True)
                if not ok:
                    return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
            order = _reload_admin_order(order.pk)
        else:
            order = _apply_status_update(order, new_status)

        record_admin_activity(
            request,
            category=AdminActivityLog.Category.ORDERS,
            action="order.status_updated",
            message=f"Updated order #{order.pk} status to {new_status}.",
            target_type="order",
            target_id=order.pk,
            metadata={"status": new_status},
        )
        payload = {
            "message": "Order status updated.",
            "order_id": order.pk,
            "status": ORDER_CUSTOMER_TRACKING_STAGE_LABELS.get(
                new_status,
                "Cancelled" if new_status == "cancelled" else new_status,
            ),
            "display_status": _display_status_for_order(order),
            "order_status": order.status,
            "customer_tracking_stage": order.customer_tracking_stage,
        }
        return Response(payload, status=status.HTTP_200_OK)


class AdminOrderCollectCodAPIView(APIView):
    """
    COD only: delivery boy collects cash → delivered + paid + Zoho invoice payment.

    Invoice must already exist in Zoho Books (staff creates it in Zoho UI).
    POST /api/admin/orders/collect-cod/?id=<order_id>
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def post(self, request):
        order_id, err = _parse_order_id_query_param(request)
        if err:
            return err
        order = get_object_or_404(_admin_orders_queryset(), pk=order_id)
        ready, reason = order_ready_for_cod_collect(order)
        if not ready:
            return Response({"detail": reason}, status=status.HTTP_400_BAD_REQUEST)

        ok, steps = finalize_cod_delivery_and_payment(order_id)
        order = _reload_admin_order(order_id)

        record_admin_activity(
            request,
            category=AdminActivityLog.Category.ORDERS,
            action="order.cod_collected",
            message=f"Collected COD cash for order #{order.pk}.",
            target_type="order",
            target_id=order.pk,
            metadata={"status": Order.CustomerTrackingStage.DELIVERED},
        )
        return Response(
            {
                "status": "success" if ok else "error",
                "message": (
                    "Cash collected. Order delivered and invoice paid."
                    if ok
                    else "Could not complete COD collection."
                ),
                "steps_completed": steps,
                "order": _admin_order_payload(order),
            },
            status=status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST,
        )


class AdminOrderTimelineAPIView(APIView):
    """GET /api/admin/orders/timeline/?id=<order_id>"""

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        order_id, err = _parse_order_id_query_param(request)
        if err:
            return err
        order = get_object_or_404(_admin_orders_queryset(), pk=order_id)
        tracking = OrderSerializer(order).data.get("tracking", {})
        return Response(
            {
                "order_id": order.pk,
                "order_code": order_code_for_order(order),
                "display_status": _display_status_for_order(order),
                "tracking": tracking,
                "timeline": _build_order_timeline(order),
                "status_history": _build_order_status_history(order),
            },
            status=status.HTTP_200_OK,
        )


class AdminOrderGeideaCollectAPIView(APIView):
    """
    Delivery boy / staff: start Geidea card payment for a card-on-delivery order.

    POST /api/admin/orders/geidea-collect/?id=<order_id>
    Order must be out_for_delivery with invoice created. Returns session_id for HPP.
    On Geidea callback: paid → delivered → notification → Zoho invoice paid.
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def post(self, request):
        order_id, err = _parse_order_id_query_param(request)
        if err:
            return err
        order = get_object_or_404(_admin_orders_queryset(), pk=order_id)
        ready, reason = order_ready_for_card_on_delivery_collect(order)
        if not ready:
            return Response({"detail": reason}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session_id = create_geidea_session(order)
        except GeideaSessionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "message": "Geidea session created. Collect card payment on device.",
                "session_id": session_id,
                "order_id": order.pk,
            },
            status=status.HTTP_200_OK,
        )


class AdminOrderCollectCardAPIView(APIView):
    """
    Card on delivery: after Geidea POS/HPP payment, verify payment and complete delivery.

    POST /api/admin/orders/collect-card/?id=<order_id>
    Body (invoice_id optional if already linked on the order):
      invoice_id          — Zoho Books invoice_id or invoice number (e.g. INV-000030)
      gateway_reference   — Geidea orderId / POS reference (optional if one paid payment exists)
      amount              — defaults to order total
      transaction_status  — defaults to paid
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def post(self, request):
        order_id, err = _parse_order_id_query_param(request)
        if err:
            return err
        order = get_object_or_404(_admin_orders_queryset(), pk=order_id)
        if not is_card_on_delivery_order(order):
            return Response(
                {"detail": "Order is not card on delivery."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = AdminOrderCollectCardSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        invoice_id = (data.get("invoice_id") or order.zoho_books_invoice_id or "").strip()
        if not invoice_id:
            return Response(
                {
                    "detail": (
                        "invoice_id is required when the order has no linked Zoho Books invoice."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        amount = data.get("amount")
        if amount is None:
            amount = Decimal(str(order.total or "0")).quantize(Decimal("0.01"))
        else:
            amount = Decimal(str(amount)).quantize(Decimal("0.01"))

        ok, steps = submit_card_on_delivery_collection(
            order_id,
            invoice_id=invoice_id,
            gateway_reference=(data.get("gateway_reference") or "").strip(),
            amount=amount,
            transaction_status=(data.get("transaction_status") or "paid").strip(),
        )
        order = _reload_admin_order(order_id)

        if ok:
            record_admin_activity(
                request,
                category=AdminActivityLog.Category.ORDERS,
                action="order.card_collected",
                message=f"Collected card payment for order #{order.pk}.",
                target_type="order",
                target_id=order.pk,
                metadata={"status": Order.CustomerTrackingStage.DELIVERED},
            )

        return Response(
            {
                "status": "success" if ok else "error",
                "message": (
                    "Card payment collected. Order delivered and invoice paid."
                    if ok
                    else (steps[-1] if steps else "Could not complete card collection.")
                ),
                "steps_completed": steps,
                "order": _admin_order_payload(order),
            },
            status=status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST,
        )


class AdminOrderGeideaReconcileAPIView(APIView):
    """
    Staff fallback when Geidea callback was missed after card collection.

    POST /api/admin/orders/geidea-reconcile/?id=<order_id>
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def post(self, request):
        order_id, err = _parse_order_id_query_param(request)
        if err:
            return err
        order = get_object_or_404(_admin_orders_queryset(), pk=order_id)
        if order.payment_method != Order.PaymentMethod.CARD_ON_DELIVERY:
            return Response(
                {"detail": "Only card-on-delivery orders use this reconcile endpoint."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        reconcile_status, steps = reconcile_missed_geidea_callback(order)
        order = _reload_admin_order(order_id)
        return Response(
            {
                "status": reconcile_status,
                "message": (
                    "Payment reconciled."
                    if reconcile_status == "paid"
                    else "Payment still pending on Geidea."
                ),
                "steps_completed": steps,
                "order": _admin_order_payload(order),
            },
            status=status.HTTP_200_OK,
        )


class AdminOrderCreateInvoiceAPIView(APIView):
    """
    Create a Zoho Books invoice from the order's sales order.

    POST /api/admin/orders/create-invoice/?id=<order_id>
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def post(self, request):
        order_id, err = _parse_order_id_query_param(request)
        if err:
            return err
        order = get_object_or_404(_admin_orders_queryset(), pk=order_id)

        ok, message = staff_create_zoho_books_invoice_for_order(order_id)
        order = _reload_admin_order(order_id)

        if ok:
            record_admin_activity(
                request,
                category=AdminActivityLog.Category.ORDERS,
                action="order.invoice_created",
                message=(
                    f"Created Zoho Books invoice for order #{order.pk} "
                    f"({order.zoho_books_invoice_number or order.zoho_books_invoice_id})."
                ),
                target_type="order",
                target_id=order.pk,
                metadata={
                    "invoice_id": order.zoho_books_invoice_id,
                    "invoice_number": order.zoho_books_invoice_number,
                },
            )

        payload = {
            "status": "success" if ok else "error",
            "message": message,
            "invoice_id": order.zoho_books_invoice_id or "",
            "invoice_number": order.zoho_books_invoice_number or "",
            "order": build_admin_order_detail_payload(order),
        }
        if (order.zoho_books_invoice_id or "").strip():
            invoice_detail, invoice_fetch_error = resolve_invoice_detail_for_order(order)
            if invoice_detail is not None:
                payload["invoice"] = invoice_detail
            if invoice_fetch_error:
                payload["invoice_fetch_error"] = invoice_fetch_error
        if ok and is_pay_on_delivery_payment_method(order.payment_method):
            if is_card_on_delivery_order(order):
                payload["next_step"] = (
                    "POST /api/admin/orders/geidea-collect/?id=<order_id> then "
                    "collect-card after Geidea payment."
                )
            else:
                payload["next_step"] = (
                    "POST /api/admin/orders/collect-cod/?id=<order_id> after cash collection."
                )

        return Response(
            payload,
            status=status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST,
        )


class AdminOrderVerifyPaymentAPIView(APIView):
    """
    Verify / record payment for an order.

    POST /api/admin/orders/verify-payment/?id=<order_id>

    - Prepaid (gateway / pay-by-link): marks payment_status paid and credits user account.
    - If invoice exists: records Zoho Books customer payment against the invoice.
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def post(self, request):
        order_id, err = _parse_order_id_query_param(request)
        if err:
            return err
        order = get_object_or_404(_admin_orders_queryset(), pk=order_id)
        if is_cod_order(order):
            return Response(
                {
                    "status": "error",
                    "message": (
                        "Cash on delivery orders use POST /api/admin/orders/collect-cod/?id=<order_id> "
                        "after the delivery boy collects cash."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
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
                order_id,
                amount=amount,
                gateway_reference=gateway_reference,
            )
            if not ok or order is None:
                return Response(
                    {"status": "error", "message": message},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            steps.append(message)
            order = _reload_admin_order(order_id)

        if (order.zoho_books_invoice_id or "").strip() and not (order.zoho_books_payment_id or "").strip():
            ok, message = staff_record_zoho_books_payment_for_order(
                order_id,
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
                        "order": _admin_order_payload(_reload_admin_order(order_id)),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            steps.append(message)
            order = _reload_admin_order(order_id)
            if order.payment_method == Order.PaymentMethod.CARD_ON_DELIVERY:
                changed, deliver_msg = maybe_auto_mark_delivered_on_payment(order_id)
                if changed:
                    steps.append(deliver_msg)
                    order = _reload_admin_order(order_id)
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


class AdminOrderMarkCodPaidAPIView(APIView):
    """
    Mark a card-on-delivery order as paid in Zoho Books by recording a
    customer payment against the order's invoice.

    After a successful payment recording, triggers journal automation
    (payment charge + VAT on charge) using COD rates from ZohoBooksStoreConfig.

    POST /api/admin/orders/mark-cod-paid/?id=<order_id>
    Body (all optional):
      payment_reference  — POS machine reference ID
      payment_date       — ISO date string (defaults to today)
      zoho_invoice_id    — Zoho Books invoice_id; if omitted, fetched via sales order ID
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def post(self, request):
        order_id, err = _parse_order_id_query_param(request)
        if err:
            return err
        pk = order_id
        from decimal import Decimal

        from django.utils import timezone as dj_tz

        from shop.models import Order
        from shop.services.zoho_books import ZohoBooksError, _books_request, books_mark_invoice_sent, store_has_books_config, zoho_books_enabled
        from shop.services.zoho_books_invoice import _resolve_customer_id
        from shop.services.zoho_books_journals import create_payment_journals_for_order
        from shop.serializers import order_code_for_order

        order = get_object_or_404(
            Order.objects.select_related('user', 'store').prefetch_related('items'),
            pk=pk,
        )

        # Guard: only COD payment methods
        cod_methods = (
            Order.PaymentMethod.CARD_ON_DELIVERY.value,
            Order.PaymentMethod.CASH_ON_DELIVERY.value,
        )
        if order.payment_method not in cod_methods:
            return Response(
                {'detail': 'This endpoint is only for COD orders.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Guard: not already paid
        if (order.zoho_books_payment_id or '').strip():
            return Response(
                {'detail': 'Payment already recorded for this order.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not zoho_books_enabled():
            return Response(
                {'detail': 'Zoho Books is disabled.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not store_has_books_config(order.store):
            return Response(
                {'detail': 'Store has no Zoho Books org configuration.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Request body fields
        payment_reference = (request.data.get('payment_reference') or '').strip()
        payment_date_raw = (request.data.get('payment_date') or '').strip()
        zoho_invoice_id = (request.data.get('zoho_invoice_id') or '').strip()

        # Parse payment date
        if payment_date_raw:
            try:
                from datetime import datetime
                payment_date = datetime.strptime(payment_date_raw, '%Y-%m-%d').date()
            except ValueError:
                return Response(
                    {'detail': 'payment_date must be in YYYY-MM-DD format.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            payment_date = dj_tz.now().date()

        # Resolve invoice ID
        if not zoho_invoice_id:
            salesorder_id = (order.zoho_books_salesorder_id or '').strip()
            if not salesorder_id:
                return Response(
                    {
                        'detail': (
                            'Order has no Zoho Books sales order ID. '
                            'Please provide zoho_invoice_id manually.'
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            salesorder_number = (order.zoho_books_salesorder_number or salesorder_id).strip()
            try:
                invoices_payload = _books_request(
                    'GET',
                    'invoices',
                    store=order.store,
                    query={'salesorder_id': salesorder_id},
                )
                invoices = invoices_payload.get('invoices') or []
                if not invoices:
                    return Response(
                        {
                            'detail': (
                                f'No invoice found in Zoho Books for sales order {salesorder_number}. '
                                'Create the invoice first or provide zoho_invoice_id.'
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                first_invoice = invoices[0]
                zoho_invoice_id = str(first_invoice.get('invoice_id') or '').strip()
                if not zoho_invoice_id:
                    return Response(
                        {'detail': 'Could not extract invoice_id from Zoho Books response.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                # If invoice is not yet confirmed/sent, mark it as sent before payment.
                # Zoho Books rejects payments against draft invoices (balance due = 0).
                # Zoho list response returns status as e.g. "draft", "sent", "overdue", "paid".
                # Only "sent", "overdue", and "partially_paid" are payable states.
                invoice_status = str(first_invoice.get('status') or '').strip().lower()
                _payable_statuses = {'sent', 'overdue', 'partially_paid'}
                if invoice_status not in _payable_statuses:
                    try:
                        books_mark_invoice_sent(zoho_invoice_id, store=order.store)
                    except ZohoBooksError as exc:
                        return Response(
                            {
                                'detail': (
                                    f'Invoice (status: {invoice_status!r}) could not be confirmed '
                                    f'before payment: {exc}'
                                )
                            },
                            status=status.HTTP_400_BAD_REQUEST,
                        )
            except ZohoBooksError as exc:
                return Response(
                    {'detail': f'Zoho Books invoice lookup failed: {exc}'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Save resolved invoice ID to order if not already set
        if not (order.zoho_books_invoice_id or '').strip():
            Order.objects.filter(pk=order.pk).update(zoho_books_invoice_id=zoho_invoice_id[:64])
            order.zoho_books_invoice_id = zoho_invoice_id[:64]

        # Resolve deposit account from config
        deposit_account_id = ''
        try:
            books_config = order.store.zoho_books_config
            deposit_account_id = (books_config.deposit_account_id or '').strip()
        except Exception:
            pass

        # Resolve customer ID
        try:
            customer_id = _resolve_customer_id(order)
        except Exception as exc:
            return Response(
                {'detail': f'Could not resolve Zoho Books customer: {exc}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pay_amount = Decimal(str(order.total or 0)).quantize(Decimal('0.01'))
        is_cash = order.payment_method == Order.PaymentMethod.CASH_ON_DELIVERY.value
        payment_mode = 'cash' if is_cash else 'creditcard'
        method_label = 'Cash on Delivery' if is_cash else 'Card on Delivery'
        description_parts = [f'AoneGt order #{order.pk}', method_label]
        if payment_reference:
            description_parts.append(f'ref {payment_reference}')
        description = ' — '.join(description_parts)[:500]

        body = {
            'customer_id': customer_id,
            'payment_mode': payment_mode,
            'amount': float(pay_amount),
            'date': payment_date.isoformat(),
            'reference_number': (payment_reference or order_code_for_order(order))[:100],
            'description': description,
            'invoices': [
                {
                    'invoice_id': zoho_invoice_id,
                    'amount_applied': float(pay_amount),
                }
            ],
        }
        if deposit_account_id:
            body['account_id'] = deposit_account_id

        try:
            payment_payload = _books_request(
                'POST', 'customerpayments', store=order.store, json_data=body,
            )
            payment = payment_payload.get('payment') or {}
            payment_id = str(payment.get('payment_id') or '').strip()
            if not payment_id:
                return Response(
                    {'detail': 'Zoho Books did not return payment_id.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except ZohoBooksError as exc:
            return Response(
                {'detail': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Persist payment info and mark order as paid
        Order.objects.filter(pk=order.pk).update(
            zoho_books_payment_id=payment_id[:64],
            zoho_books_paid_at=dj_tz.now(),
            zoho_books_payment_error='',
            payment_status=Order.PaymentStatus.PAID,
        )
        order.zoho_books_payment_id = payment_id
        order.payment_status = Order.PaymentStatus.PAID

        # Journal automation — best-effort
        try:
            create_payment_journals_for_order(order, order.payment_method)
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).exception(
                'zoho-journals: COD journal trigger failed order=%s error=%s',
                order.pk, exc,
            )

        order = _reload_admin_order(pk)
        return Response(
            {
                'message': 'Invoice marked as paid.',
                'payment_id': payment_id,
                'invoice_id': zoho_invoice_id,
                'order': _admin_order_payload(order),
            },
            status=status.HTTP_200_OK,
        )
