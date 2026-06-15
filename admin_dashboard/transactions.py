from decimal import Decimal
from typing import Optional, Tuple

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from shop.models import AccountCreditLedger
from shop.serializers import order_code_for_order

from .orders import _apply_order_date_filter, _paginate_queryset
from .returns import _customer_display_name
from .views import IsStaffUser

_KIND_ALIASES = {
    "gateway": AccountCreditLedger.Kind.GATEWAY_PAYMENT,
    "gateway_payment": AccountCreditLedger.Kind.GATEWAY_PAYMENT,
    "paylink": AccountCreditLedger.Kind.PAYLINK_PAYMENT,
    "paylink_payment": AccountCreditLedger.Kind.PAYLINK_PAYMENT,
    "pay_by_link": AccountCreditLedger.Kind.PAYLINK_PAYMENT,
    "invoice": AccountCreditLedger.Kind.INVOICE_APPLICATION,
    "invoice_application": AccountCreditLedger.Kind.INVOICE_APPLICATION,
    "cancel": AccountCreditLedger.Kind.ORDER_CANCEL,
    "order_cancel": AccountCreditLedger.Kind.ORDER_CANCEL,
    "cancelled": AccountCreditLedger.Kind.ORDER_CANCEL,
    "admin": AccountCreditLedger.Kind.ADMIN_ADJUSTMENT,
    "admin_adjustment": AccountCreditLedger.Kind.ADMIN_ADJUSTMENT,
    "refund": AccountCreditLedger.Kind.ADMIN_ADJUSTMENT,
    "return_refund": AccountCreditLedger.Kind.ADMIN_ADJUSTMENT,
}


def _transactions_queryset():
    return AccountCreditLedger.objects.select_related("user", "order", "order__store")


def _normalize_transaction_kind(raw: str) -> Optional[str]:
    key = (raw or "").strip().lower()
    if not key:
        return None
    if key in _KIND_ALIASES:
        return _KIND_ALIASES[key]
    for choice in AccountCreditLedger.Kind:
        if key == choice.value or key == choice.label.lower():
            return choice.value
    return None


def _transaction_direction(amount: Decimal) -> str:
    return "credit" if amount > 0 else "debit"


def _quantize_amount(value) -> str:
    return str(Decimal(str(value or 0)).quantize(Decimal("0.01")))


class AdminTransactionListSerializer(serializers.ModelSerializer):
    transaction_id = serializers.IntegerField(source="id", read_only=True)
    customer_id = serializers.IntegerField(source="user_id", read_only=True)
    customer_email = serializers.EmailField(source="user.email", read_only=True)
    customer_name = serializers.SerializerMethodField()
    order_id = serializers.IntegerField(read_only=True, allow_null=True)
    order_code = serializers.SerializerMethodField()
    store_id = serializers.SerializerMethodField()
    store_name = serializers.SerializerMethodField()
    kind_label = serializers.SerializerMethodField()
    direction = serializers.SerializerMethodField()
    amount_aed = serializers.SerializerMethodField()
    amount_abs_aed = serializers.SerializerMethodField()
    balance_after_aed = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    is_return_refund = serializers.SerializerMethodField()

    class Meta:
        model = AccountCreditLedger
        fields = (
            "transaction_id",
            "customer_id",
            "customer_email",
            "customer_name",
            "order_id",
            "order_code",
            "store_id",
            "store_name",
            "kind",
            "kind_label",
            "direction",
            "amount_aed",
            "amount_abs_aed",
            "balance_after_aed",
            "reference",
            "note",
            "currency",
            "is_return_refund",
            "created_at",
        )

    def get_customer_name(self, obj):
        return _customer_display_name(obj.user)

    def get_order_code(self, obj):
        return order_code_for_order(obj.order) if obj.order_id else None

    def get_store_id(self, obj):
        return obj.order.store_id if obj.order_id else None

    def get_store_name(self, obj):
        return obj.order.store.name if obj.order_id and obj.order.store_id else None

    def get_kind_label(self, obj):
        return obj.get_kind_display()

    def get_direction(self, obj):
        return _transaction_direction(Decimal(str(obj.amount)))

    def get_amount_aed(self, obj):
        return _quantize_amount(obj.amount)

    def get_amount_abs_aed(self, obj):
        return _quantize_amount(abs(Decimal(str(obj.amount))))

    def get_balance_after_aed(self, obj):
        return _quantize_amount(obj.balance_after)

    def get_currency(self, obj):
        return (obj.order.currency if obj.order_id else None) or "AED"

    def get_is_return_refund(self, obj):
        return (
            obj.kind == AccountCreditLedger.Kind.ADMIN_ADJUSTMENT
            and (obj.reference or "").startswith("return:")
        )


def _parse_transaction_id_query_param(request, *, required=True):
    transaction_id = (request.query_params.get('id') or '').strip()
    if not transaction_id:
        if required:
            return None, Response(
                {'detail': 'Query parameter id is required and must be a positive integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None, None
    if not transaction_id.isdigit():
        return None, Response(
            {'detail': 'Query parameter id is required and must be a positive integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return int(transaction_id), None


def _apply_transaction_list_filters(queryset, request) -> Tuple[object, Optional[str], Optional[dict]]:
    kind_filter = (request.query_params.get("kind") or request.query_params.get("type") or "").strip()
    if kind_filter:
        normalized = _normalize_transaction_kind(kind_filter)
        if normalized:
            queryset = queryset.filter(kind=normalized)
        elif kind_filter.lower() == "return_refund":
            queryset = queryset.filter(
                kind=AccountCreditLedger.Kind.ADMIN_ADJUSTMENT,
                reference__startswith="return:",
            )

    direction = (request.query_params.get("direction") or "").strip().lower()
    if direction == "credit":
        queryset = queryset.filter(amount__gt=0)
    elif direction == "debit":
        queryset = queryset.filter(amount__lt=0)

    search = (request.query_params.get("search") or "").strip()
    if search:
        q = (
            Q(user__email__icontains=search)
            | Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
            | Q(reference__icontains=search)
            | Q(note__icontains=search)
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

    return _apply_order_date_filter(queryset, request)


def _transaction_detail_payload(entry: AccountCreditLedger) -> dict:
    data = AdminTransactionListSerializer(entry).data
    data["customer"] = {
        "id": entry.user_id,
        "email": entry.user.email,
        "first_name": entry.user.first_name,
        "last_name": entry.user.last_name,
        "phone": entry.user.phone,
        "credit_balance_aed": _quantize_amount(entry.user.credit_balance_aed),
    }
    if entry.order_id:
        data["order"] = {
            "id": entry.order_id,
            "order_code": order_code_for_order(entry.order),
            "total": _quantize_amount(entry.order.total),
            "payment_method": entry.order.payment_method,
            "payment_status": entry.order.payment_status,
            "gateway_reference": entry.order.gateway_reference or "",
            "store_id": entry.order.store_id,
            "store_name": entry.order.store.name if entry.order.store_id else "",
            "status": entry.order.status,
            "created_at": entry.order.created_at.isoformat() if entry.order.created_at else None,
        }
    else:
        data["order"] = None
    return data


class AdminTransactionListAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        if (request.query_params.get('id') or '').strip():
            transaction_id, err = _parse_transaction_id_query_param(request)
            if err:
                return err
            entry = get_object_or_404(_transactions_queryset(), pk=transaction_id)
            return Response(_transaction_detail_payload(entry), status=status.HTTP_200_OK)

        qs, date_err, date_filter = _apply_transaction_list_filters(
            _transactions_queryset().order_by("-created_at"),
            request,
        )
        if date_err:
            return Response({"detail": date_err}, status=status.HTTP_400_BAD_REQUEST)

        page_qs, pagination = _paginate_queryset(qs, request)
        payload = {
            **pagination,
            "results": AdminTransactionListSerializer(page_qs, many=True).data,
        }
        if date_filter:
            payload["date_filter"] = date_filter
        return Response(payload, status=status.HTTP_200_OK)


class AdminTransactionDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        transaction_id, err = _parse_transaction_id_query_param(request)
        if err:
            return err
        entry = get_object_or_404(_transactions_queryset(), pk=transaction_id)
        return Response(_transaction_detail_payload(entry), status=status.HTTP_200_OK)
