from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from shop.models import Order, OrderReturn, SupportTicket, UserAddress

from .activity_log_utils import record_admin_activity
from .models import AdminActivityLog
from .orders import AdminOrderListSerializer, _paginate_queryset
from .returns import AdminReturnListSerializer
from .super_coins import build_customer_super_coins_payload
from .views import IsStaffUser

User = get_user_model()


def _customers_queryset():
    return User.objects.filter(is_staff=False, is_superuser=False)


def _customer_display_name(user) -> str:
    parts = [user.first_name or "", user.last_name or ""]
    return " ".join(p for p in parts if p).strip() or user.email


def _customer_status_label(user) -> str:
    return "active" if user.is_active else "inactive"


class AdminCustomerListSerializer(serializers.ModelSerializer):
    customer_id = serializers.IntegerField(source="id", read_only=True)
    name = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    super_coins_balance = serializers.IntegerField(source="points_balance", read_only=True)
    orders_count = serializers.IntegerField(read_only=True)
    total_spent = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "customer_id",
            "email",
            "name",
            "phone",
            "status",
            "is_active",
            "super_coins_balance",
            "credit_balance_aed",
            "orders_count",
            "total_spent",
            "created_at",
        )

    def get_name(self, obj):
        return _customer_display_name(obj)

    def get_status(self, obj):
        return _customer_status_label(obj)

    def get_total_spent(self, obj):
        total = getattr(obj, "orders_total", None)
        if total is None:
            total = (
                Order.objects.filter(user=obj)
                .exclude(status=Order.Status.CANCELLED)
                .aggregate(v=Sum("total"))["v"]
                or Decimal("0")
            )
        return str(Decimal(str(total)).quantize(Decimal("0.01")))


class AdminCustomerDetailSerializer(AdminCustomerListSerializer):
    zoho_books_contact_id = serializers.CharField(read_only=True)
    last_login = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta(AdminCustomerListSerializer.Meta):
        fields = AdminCustomerListSerializer.Meta.fields + (
            "zoho_books_contact_id",
            "last_login",
        )


class AdminCustomerAddressSerializer(serializers.ModelSerializer):
    address_id = serializers.IntegerField(source="id", read_only=True)

    class Meta:
        model = UserAddress
        fields = (
            "address_id",
            "full_name",
            "phone_number",
            "address",
            "city",
            "state",
            "address_type",
            "is_default",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class AdminCustomerStatusUpdateSerializer(serializers.Serializer):
    status = serializers.CharField()

    def validate_status(self, value):
        key = (value or "").strip().lower()
        allowed = {"active", "inactive", "blocked"}
        if key not in allowed:
            raise serializers.ValidationError(
                "Invalid status. Allowed values: active, inactive, blocked."
            )
        return key


def _parse_customer_id_query_param(request, *, required=True):
    customer_id = (request.query_params.get('id') or '').strip()
    if not customer_id:
        if required:
            return None, Response(
                {'detail': 'Query parameter id is required and must be a positive integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None, None
    if not customer_id.isdigit():
        return None, Response(
            {'detail': 'Query parameter id is required and must be a positive integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return int(customer_id), None


def _customer_detail_queryset():
    return _customers_queryset().annotate(
        orders_count=Count("orders", distinct=True),
        orders_total=Sum(
            "orders__total",
            filter=~Q(orders__status=Order.Status.CANCELLED),
        ),
    )


def build_customer_detail_payload(user) -> dict:
    """Full customer profile for admin detail view."""
    addresses = UserAddress.objects.filter(user=user).order_by(
        "-is_default", "-updated_at", "-created_at",
    )
    orders = (
        Order.objects.filter(user=user)
        .select_related("user", "store")
        .prefetch_related("items", "returns__lines__order_item")
        .order_by("-created_at")
    )
    returns = (
        OrderReturn.objects.filter(user=user)
        .select_related("user", "order", "order__store")
        .prefetch_related("lines__order_item")
        .order_by("-created_at")
    )
    status_path = f"/api/admin/customers/status/?id={user.pk}"
    is_active = user.is_active

    return {
        **AdminCustomerDetailSerializer(user).data,
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "delivery_addresses": AdminCustomerAddressSerializer(addresses, many=True).data,
        "orders": AdminOrderListSerializer(orders, many=True).data,
        "return_requests": AdminReturnListSerializer(returns, many=True).data,
        "support_tickets_summary": {
            "total_count": support_tickets_qs.count(),
            "open_count": support_tickets_qs.filter(status__in=open_ticket_statuses).count(),
            "list_path": f"/api/admin/customers/support-tickets/by-customer/?customer_id={user.pk}",
        },
        "actions": {
            "update_status": {
                "method": "PATCH",
                "path": status_path,
                "allowed_statuses": ["active", "inactive", "blocked"],
                "current_status": _customer_status_label(user),
            },
            "activate": {
                "method": "PATCH",
                "path": status_path,
                "body": {"status": "active"},
                "available": not is_active,
            },
            "deactivate": {
                "method": "PATCH",
                "path": status_path,
                "body": {"status": "inactive"},
                "available": is_active,
            },
        },
    }


def _apply_customer_list_filters(queryset, request):
    status_filter = (request.query_params.get("status") or "").strip().lower()
    if status_filter == "active":
        queryset = queryset.filter(is_active=True)
    elif status_filter in ("inactive", "blocked"):
        queryset = queryset.filter(is_active=False)

    search = (request.query_params.get("search") or "").strip()
    if search:
        q = (
            Q(email__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(phone__icontains=search)
        )
        if search.isdigit():
            q |= Q(pk=int(search))
        queryset = queryset.filter(q)

    return queryset.annotate(
        orders_count=Count("orders", distinct=True),
        orders_total=Sum(
            "orders__total",
            filter=~Q(orders__status=Order.Status.CANCELLED),
        ),
    )


class AdminCustomerListAPIView(APIView):
    """
    GET /api/admin/customers/          — paginated list
    GET /api/admin/customers/?id=<id> — full customer profile (detail)
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        if (request.query_params.get("id") or "").strip():
            customer_id, err = _parse_customer_id_query_param(request)
            if err:
                return err
            user = get_object_or_404(_customer_detail_queryset(), pk=customer_id)
            return Response(build_customer_detail_payload(user), status=status.HTTP_200_OK)

        qs = _apply_customer_list_filters(
            _customers_queryset().order_by("-created_at"),
            request,
        )
        page_qs, pagination = _paginate_queryset(qs, request)
        return Response(
            {
                **pagination,
                "results": AdminCustomerListSerializer(page_qs, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class AdminCustomerDetailAPIView(APIView):
    """GET /api/admin/customers/detail/?id=<customer_id> — same payload as /customers/?id="""

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        customer_id, err = _parse_customer_id_query_param(request)
        if err:
            return err
        user = get_object_or_404(_customer_detail_queryset(), pk=customer_id)
        return Response(build_customer_detail_payload(user), status=status.HTTP_200_OK)


class AdminCustomerStatusUpdateAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def patch(self, request):
        customer_id, err = _parse_customer_id_query_param(request)
        if err:
            return err
        user = get_object_or_404(_customers_queryset(), pk=customer_id)
        serializer = AdminCustomerStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]
        user.is_active = new_status == "active"
        user.save(update_fields=["is_active"])
        record_admin_activity(
            request,
            category=AdminActivityLog.Category.CUSTOMERS,
            action="customer.status_updated",
            message=f"Updated customer #{user.pk} status to {new_status}.",
            target_type="customer",
            target_id=user.pk,
            metadata={"status": new_status},
        )
        return Response(
            {
                "message": "Customer status updated.",
                "customer_id": user.pk,
                "status": _customer_status_label(user),
                "is_active": user.is_active,
            },
            status=status.HTTP_200_OK,
        )


class AdminCustomerOrdersAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        customer_id, err = _parse_customer_id_query_param(request)
        if err:
            return err
        user = get_object_or_404(_customers_queryset(), pk=customer_id)
        qs = (
            Order.objects.filter(user=user)
            .select_related("user", "store")
            .prefetch_related("items", "returns__lines__order_item")
            .order_by("-created_at")
        )
        store_id = (request.query_params.get("store_id") or "").strip()
        if store_id.isdigit():
            qs = qs.filter(store_id=int(store_id))

        page_qs, pagination = _paginate_queryset(qs, request)
        return Response(
            {
                "customer_id": user.pk,
                **pagination,
                "results": AdminOrderListSerializer(page_qs, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class AdminCustomerSuperCoinsAPIView(APIView):
    """Super coins = loyalty points wallet (points_balance) and history."""

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        customer_id, err = _parse_customer_id_query_param(request)
        if err:
            return err
        user = get_object_or_404(_customers_queryset(), pk=customer_id)
        return Response(build_customer_super_coins_payload(user), status=status.HTTP_200_OK)
