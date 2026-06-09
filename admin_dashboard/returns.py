from datetime import timedelta
from decimal import Decimal
from typing import Optional

import logging

from django.contrib.auth import get_user_model
from django.db import transaction

logger = logging.getLogger(__name__)
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from shop.models import AccountCreditLedger, Order, OrderReturn, UserNotification
from shop.serializers import OrderReturnReadSerializer, order_code_for_order
from shop.services.account_credit import get_user_credit_balance
from shop.services.notifications import create_user_notification
from shop.services.order_email import send_refund_email
from shop.services.zoho_returns import enqueue_push_return_to_zoho

from .orders import _paginate_queryset, _parse_order_list_date
from .views import IsStaffUser

User = get_user_model()

_RETURN_STATUS_ALIASES = {
    "pending": OrderReturn.Status.PENDING_ZOHO,
    "pending_zoho": OrderReturn.Status.PENDING_ZOHO,
    "approved": OrderReturn.Status.SYNCED,
    "synced": OrderReturn.Status.SYNCED,
    "completed": OrderReturn.Status.COMPLETED,
    "rejected": OrderReturn.Status.REJECTED,
    "failed": OrderReturn.Status.FAILED,
}

_APPROVABLE_STATUSES = (
    OrderReturn.Status.PENDING_ZOHO,
    OrderReturn.Status.FAILED,
)
_REJECTABLE_STATUSES = (
    OrderReturn.Status.PENDING_ZOHO,
    OrderReturn.Status.SYNCED,
    OrderReturn.Status.FAILED,
)


def _admin_returns_queryset():
    return (
        OrderReturn.objects.select_related("order", "order__store", "user")
        .prefetch_related("lines__order_item")
    )


def _reload_admin_return(pk: int) -> OrderReturn:
    return get_object_or_404(_admin_returns_queryset(), pk=pk)


def _normalize_return_status(raw: str) -> Optional[str]:
    key = (raw or "").strip().lower()
    if not key:
        return None
    if key in _RETURN_STATUS_ALIASES:
        return _RETURN_STATUS_ALIASES[key]
    for choice in OrderReturn.Status:
        if key == choice.value or key == choice.label.lower():
            return choice.value
    return None


def _return_refund_amount(ret: OrderReturn) -> Decimal:
    total = Decimal("0")
    for line in ret.lines.all():
        total += Decimal(str(line.order_item.unit_price)) * int(line.quantity)
    return total.quantize(Decimal("0.01"))


def _customer_display_name(user) -> str:
    parts = [user.first_name or "", user.last_name or ""]
    return " ".join(p for p in parts if p).strip() or user.email


def _return_reason_label(ret: OrderReturn) -> str:
    raw = (ret.return_reason or "").strip()
    if not raw:
        return ""
    try:
        return OrderReturn.ReturnReason(raw).label
    except ValueError:
        return raw


def _apply_return_date_filter(queryset, request):
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


def _apply_return_list_filters(queryset, request):
    status_filter = (request.query_params.get("status") or "").strip()
    if status_filter:
        normalized = _normalize_return_status(status_filter)
        if normalized:
            queryset = queryset.filter(status=normalized)

    search = (request.query_params.get("search") or "").strip()
    if search:
        q = (
            Q(user__email__icontains=search)
            | Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
            | Q(note__icontains=search)
            | Q(return_reason_detail__icontains=search)
        )
        if search.isdigit():
            sid = int(search)
            q |= Q(pk=sid) | Q(order_id=sid) | Q(user_id=sid)
        queryset = queryset.filter(q)

    order_id = (request.query_params.get("order_id") or "").strip()
    if order_id.isdigit():
        queryset = queryset.filter(order_id=int(order_id))

    customer_id = (
        request.query_params.get("customer_id")
        or request.query_params.get("user_id")
        or ""
    ).strip()
    if customer_id.isdigit():
        queryset = queryset.filter(user_id=int(customer_id))

    store_id = (request.query_params.get("store_id") or "").strip()
    if store_id.isdigit():
        queryset = queryset.filter(order__store_id=int(store_id))

    return _apply_return_date_filter(queryset, request)


def _credit_return_refund(user, amount: Decimal, *, order_return: OrderReturn, note: str = "") -> Decimal:
    amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValueError("Refund amount must be greater than zero.")

    user = User.objects.select_for_update().get(pk=user.pk)
    new_balance = get_user_credit_balance(user) + amount
    user.credit_balance_aed = new_balance
    user.save(update_fields=["credit_balance_aed"])

    AccountCreditLedger.objects.create(
        user=user,
        order=order_return.order,
        kind=AccountCreditLedger.Kind.ADMIN_ADJUSTMENT,
        amount=amount,
        balance_after=new_balance,
        reference=f"return:{order_return.pk}"[:255],
        note=note or f"Return #{order_return.pk} refund",
    )
    return new_balance


class AdminReturnNoteSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")


class AdminReturnRefundSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        allow_null=True,
    )
    note = serializers.CharField(required=False, allow_blank=True, default="")


class AdminReturnListSerializer(serializers.ModelSerializer):
    return_id = serializers.IntegerField(source="id", read_only=True)
    order_id = serializers.IntegerField(read_only=True)
    order_code = serializers.SerializerMethodField()
    customer_id = serializers.IntegerField(source="user_id", read_only=True)
    customer_email = serializers.EmailField(source="user.email", read_only=True)
    customer_name = serializers.SerializerMethodField()
    store_id = serializers.IntegerField(source="order.store_id", read_only=True)
    store_name = serializers.CharField(source="order.store.name", read_only=True)
    status_label = serializers.SerializerMethodField()
    return_reason_label = serializers.SerializerMethodField()
    refund_amount = serializers.SerializerMethodField()
    lines_count = serializers.SerializerMethodField()

    class Meta:
        model = OrderReturn
        fields = (
            "return_id",
            "order_id",
            "order_code",
            "customer_id",
            "customer_email",
            "customer_name",
            "store_id",
            "store_name",
            "status",
            "status_label",
            "return_reason",
            "return_reason_label",
            "return_reason_detail",
            "refund_amount",
            "lines_count",
            "zoho_salesreturn_id",
            "created_at",
            "updated_at",
        )

    def get_order_code(self, obj):
        return order_code_for_order(obj.order)

    def get_customer_name(self, obj):
        return _customer_display_name(obj.user)

    def get_status_label(self, obj):
        return obj.get_status_display()

    def get_return_reason_label(self, obj):
        return _return_reason_label(obj)

    def get_refund_amount(self, obj):
        return str(_return_refund_amount(obj))

    def get_lines_count(self, obj):
        return obj.lines.count()


class AdminReturnDetailAPIViewMixin:
    def _detail_payload(self, ret: OrderReturn) -> dict:
        data = AdminReturnListSerializer(ret).data
        data["note"] = ret.note or ""
        data["lines"] = OrderReturnReadSerializer(ret).data.get("lines", [])
        data["currency"] = ret.order.currency or "AED"
        data["customer"] = {
            "id": ret.user_id,
            "email": ret.user.email,
            "first_name": ret.user.first_name,
            "last_name": ret.user.last_name,
            "phone": ret.user.phone,
        }
        data["order"] = {
            "id": ret.order_id,
            "order_code": order_code_for_order(ret.order),
            "total": str(Decimal(str(ret.order.total)).quantize(Decimal("0.01"))),
            "payment_method": ret.order.payment_method,
            "payment_status": ret.order.payment_status,
            "display_status": ret.order.get_status_display(),
        }
        return data


class AdminReturnListAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        qs, date_err, date_filter = _apply_return_list_filters(
            _admin_returns_queryset().order_by("-created_at"),
            request,
        )
        if date_err:
            return Response({"detail": date_err}, status=status.HTTP_400_BAD_REQUEST)

        page_qs, pagination = _paginate_queryset(qs, request)
        payload = {
            **pagination,
            "results": AdminReturnListSerializer(page_qs, many=True).data,
        }
        if date_filter:
            payload["date_filter"] = date_filter
        return Response(payload, status=status.HTTP_200_OK)


class AdminReturnDetailAPIView(AdminReturnDetailAPIViewMixin, APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request, pk):
        ret = _reload_admin_return(pk)
        return Response(self._detail_payload(ret), status=status.HTTP_200_OK)


class AdminReturnApproveAPIView(AdminReturnDetailAPIViewMixin, APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def patch(self, request, pk):
        serializer = AdminReturnNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        staff_note = (serializer.validated_data.get("note") or "").strip()

        with transaction.atomic():
            ret = get_object_or_404(_admin_returns_queryset().select_for_update(), pk=pk)
            if ret.status not in _APPROVABLE_STATUSES:
                return Response(
                    {
                        "detail": (
                            f"Return cannot be approved from status "
                            f"'{ret.get_status_display()}'."
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if staff_note:
                ret.note = (
                    f"{ret.note}\n[Approved] {staff_note}".strip()
                    if ret.note
                    else f"[Approved] {staff_note}"
                )

            ret.status = OrderReturn.Status.SYNCED
            ret.save(update_fields=["status", "note", "updated_at"])

        enqueue_push_return_to_zoho(ret.pk)

        create_user_notification(
            ret.user,
            UserNotification.Kind.ORDER,
            title="Return approved",
            body="Your return request was approved. Refund will be processed shortly.",
            payload={
                "event": "return_approved",
                "return_id": ret.pk,
                "order_id": ret.order_id,
                "order_code": order_code_for_order(ret.order),
            },
        )

        ret = _reload_admin_return(pk)
        return Response(
            {
                "message": "Return approved.",
                "return": self._detail_payload(ret),
            },
            status=status.HTTP_200_OK,
        )


class AdminReturnRejectAPIView(AdminReturnDetailAPIViewMixin, APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def patch(self, request, pk):
        with transaction.atomic():
            ret = get_object_or_404(_admin_returns_queryset().select_for_update(), pk=pk)
            if ret.status not in _REJECTABLE_STATUSES:
                return Response(
                    {
                        "detail": (
                            f"Return cannot be rejected from status "
                            f"'{ret.get_status_display()}'."
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer = AdminReturnNoteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            staff_note = (serializer.validated_data.get("note") or "").strip()
            if staff_note:
                ret.note = (
                    f"{ret.note}\n[Rejected] {staff_note}".strip()
                    if ret.note
                    else f"[Rejected] {staff_note}"
                )

            ret.status = OrderReturn.Status.REJECTED
            ret.save(update_fields=["status", "note", "updated_at"])

        create_user_notification(
            ret.user,
            UserNotification.Kind.ORDER,
            title="Return rejected",
            body=staff_note or "Your return request was not approved.",
            payload={
                "event": "return_rejected",
                "return_id": ret.pk,
                "order_id": ret.order_id,
                "order_code": order_code_for_order(ret.order),
            },
        )

        ret = _reload_admin_return(pk)
        return Response(
            {
                "message": "Return rejected.",
                "return": self._detail_payload(ret),
            },
            status=status.HTTP_200_OK,
        )


class AdminReturnRefundAPIView(AdminReturnDetailAPIViewMixin, APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def post(self, request, pk):
        serializer = AdminReturnRefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            ret = get_object_or_404(_admin_returns_queryset().select_for_update(), pk=pk)

            if ret.status == OrderReturn.Status.COMPLETED:
                ret = _reload_admin_return(pk)
                # Check if already processed via Geidea
                if (ret.geidea_refund_id or '').strip():
                    return Response(
                        {
                            'message': 'Refund already processed.',
                            'refund_amount': str(_return_refund_amount(ret)),
                            'geidea_refund_id': ret.geidea_refund_id,
                            'return': self._detail_payload(ret),
                        },
                        status=status.HTTP_200_OK,
                    )
                return Response(
                    {
                        'message': 'Return already refunded.',
                        'refund_amount': str(_return_refund_amount(ret)),
                        'credit_balance_aed': str(get_user_credit_balance(ret.user)),
                        'return': self._detail_payload(ret),
                    },
                    status=status.HTTP_200_OK,
                )

            if ret.status != OrderReturn.Status.SYNCED:
                return Response(
                    {
                        'detail': (
                            'Return must be approved (status synced) before refund. '
                            f'Current status: {ret.get_status_display()}.'
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            default_amount = _return_refund_amount(ret)
            amount = serializer.validated_data.get('amount')
            if amount is None:
                refund_amount = default_amount
            else:
                refund_amount = Decimal(str(amount)).quantize(Decimal('0.01'))
            if refund_amount <= 0:
                return Response(
                    {'detail': 'Refund amount must be greater than zero.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if refund_amount > default_amount:
                return Response(
                    {
                        'detail': (
                            f'Refund amount cannot exceed return total ({default_amount} AED).'
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            staff_note = (serializer.validated_data.get('note') or '').strip()
            order = ret.order

            # Determine refund path: card payment → Geidea Refund API
            # COD / card-on-delivery → existing account credit path
            is_card_payment = order.payment_method in (
                Order.PaymentMethod.PAY_BY_LINK,
                Order.PaymentMethod.PAYMENT_GATEWAY,
            )

            if is_card_payment:
                has_gateway_ref = bool((order.gateway_reference or '').strip())
                if not has_gateway_ref:
                    return Response(
                        {
                            'detail': (
                                'No gateway_reference — card refund not possible. '
                                'Use manual credit.'
                            ),
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                # Release atomic lock before making external API call
                # (atomic block exits here for card path)

        # ── Card refund path — Geidea Refund API (outside atomic) ──────────
        if is_card_payment:
            from shop.services.geidea_paybylink import (
                GeideaRefundAlreadyProcessedError,
                GeideaRefundError,
                refund_geidea_payment,
            )

            # Re-fetch without lock for the API call
            ret = _reload_admin_return(pk)
            order = ret.order

            try:
                geidea_refund_id = refund_geidea_payment(ret, refund_amount)
            except GeideaRefundAlreadyProcessedError:
                ret = _reload_admin_return(pk)
                return Response(
                    {
                        'message': 'Refund already processed.',
                        'refund_amount': str(refund_amount),
                        'geidea_refund_id': ret.geidea_refund_id,
                        'return': self._detail_payload(ret),
                    },
                    status=status.HTTP_200_OK,
                )
            except GeideaRefundError as exc:
                logger.error(
                    'admin-refund: Geidea refund failed return=%s order=%s error=%s',
                    ret.pk, order.pk, exc,
                )
                return Response(
                    {'detail': str(exc)},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

            # Update return status and note
            with transaction.atomic():
                ret = get_object_or_404(
                    _admin_returns_queryset().select_for_update(), pk=pk,
                )
                if staff_note:
                    ret.note = (
                        f'{ret.note}\n[Refunded] {staff_note}'.strip()
                        if ret.note
                        else f'[Refunded] {staff_note}'
                    )
                ret.status = OrderReturn.Status.COMPLETED
                ret.save(update_fields=['status', 'note', 'updated_at'])

            create_user_notification(
                ret.user,
                UserNotification.Kind.ORDER,
                title='Refund initiated',
                body=(
                    f'Your refund of AED {refund_amount} will be credited to '
                    f'your card in 3–7 business days.'
                ),
                payload={
                    'event': 'return_refunded',
                    'return_id': ret.pk,
                    'order_id': ret.order_id,
                    'order_code': order_code_for_order(ret.order),
                    'refund_amount': str(refund_amount),
                },
            )

            # Best-effort refund email
            try:
                send_refund_email(order, ret.user, refund_amount)
            except Exception:
                pass

            ret = _reload_admin_return(pk)
            return Response(
                {
                    'message': 'Refund issued to customer card.',
                    'refund_amount': str(refund_amount),
                    'geidea_refund_id': geidea_refund_id,
                    'return': self._detail_payload(ret),
                },
                status=status.HTTP_200_OK,
            )

        # ── COD / card-on-delivery path — account credit (unchanged) ────────
        ledger_note = staff_note or f'Return #{ret.pk} refund ({refund_amount} AED)'

        with transaction.atomic():
            ret = get_object_or_404(_admin_returns_queryset().select_for_update(), pk=pk)

            try:
                new_balance = _credit_return_refund(
                    ret.user,
                    refund_amount,
                    order_return=ret,
                    note=ledger_note,
                )
            except ValueError as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

            if staff_note:
                ret.note = (
                    f'{ret.note}\n[Refunded] {staff_note}'.strip()
                    if ret.note
                    else f'[Refunded] {staff_note}'
                )

            ret.status = OrderReturn.Status.COMPLETED
            ret.save(update_fields=['status', 'note', 'updated_at'])

        create_user_notification(
            ret.user,
            UserNotification.Kind.ORDER,
            title='Return refund processed',
            body=f'{refund_amount} AED was credited to your account.',
            payload={
                'event': 'return_refunded',
                'return_id': ret.pk,
                'order_id': ret.order_id,
                'order_code': order_code_for_order(ret.order),
                'refund_amount': str(refund_amount),
            },
        )

        ret = _reload_admin_return(pk)
        return Response(
            {
                'message': 'Return refunded and credited to customer account.',
                'refund_amount': str(refund_amount),
                'credit_balance_aed': str(new_balance),
                'return': self._detail_payload(ret),
            },
            status=status.HTTP_200_OK,
        )
