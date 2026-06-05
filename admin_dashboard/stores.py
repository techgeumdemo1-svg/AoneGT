from typing import List, Optional

from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Store

from .orders import _paginate_queryset
from .views import IsStaffUser


def _admin_stores_queryset():
    return Store.objects.annotate(products_count=Count("products", distinct=True))


class AdminStoreListSerializer(serializers.ModelSerializer):
    store_id = serializers.IntegerField(source="id", read_only=True)
    is_visible = serializers.BooleanField(source="is_active", read_only=True)

    class Meta:
        model = Store
        fields = (
            "store_id",
            "name",
            "slug",
            "contact_email",
            "category",
            "description",
            "logo_url",
            "is_active",
            "is_visible",
            "sort_order",
            "products_count",
            "zoho_org_id",
            "zoho_store_domain",
            "zoho_books_org_id",
            "created_at",
        )


def _apply_store_list_filters(queryset, request):
    visible = (request.query_params.get("visible") or "").strip().lower()
    is_active = (request.query_params.get("is_active") or "").strip().lower()
    if visible in ("true", "1", "yes") or is_active in ("true", "1", "yes"):
        queryset = queryset.filter(is_active=True)
    elif visible in ("false", "0", "no") or is_active in ("false", "0", "no"):
        queryset = queryset.filter(is_active=False)

    search = (request.query_params.get("search") or "").strip()
    if search:
        q = Q(name__icontains=search) | Q(slug__icontains=search) | Q(category__icontains=search)
        if search.isdigit():
            q |= Q(pk=int(search))
        queryset = queryset.filter(q)

    return queryset.order_by("sort_order", "name")


class AdminStoreVisibilitySerializer(serializers.Serializer):
    is_active = serializers.BooleanField(required=False)
    visible = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if "is_active" not in attrs and "visible" not in attrs:
            raise serializers.ValidationError(
                "Provide is_active or visible (boolean)."
            )
        if "visible" in attrs:
            attrs["is_active"] = attrs["visible"]
        return attrs


class AdminStoreReorderItemSerializer(serializers.Serializer):
    id = serializers.IntegerField(min_value=1)
    sort_order = serializers.IntegerField(min_value=0)


class AdminStoreReorderSerializer(serializers.Serializer):
    """
    Reorder stores by explicit sort_order values or by ordered id list.

    Examples:
      {"order": [3, 1, 2]}
      {"stores": [{"id": 3, "sort_order": 0}, {"id": 1, "sort_order": 1}]}
    """

    order = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=False,
    )
    stores = AdminStoreReorderItemSerializer(many=True, required=False)

    def validate(self, attrs):
        order = attrs.get("order")
        stores = attrs.get("stores")
        if not order and not stores:
            raise serializers.ValidationError(
                "Provide order (list of store ids) or stores (id + sort_order)."
            )
        if order and stores:
            raise serializers.ValidationError(
                "Provide only one of order or stores, not both."
            )
        if order:
            if len(order) != len(set(order)):
                raise serializers.ValidationError("order must not contain duplicate ids.")
        if stores:
            ids = [row["id"] for row in stores]
            if len(ids) != len(set(ids)):
                raise serializers.ValidationError("stores must not contain duplicate ids.")
        return attrs


class AdminStoreListAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        qs = _apply_store_list_filters(_admin_stores_queryset(), request)
        page_qs, pagination = _paginate_queryset(qs, request)
        return Response(
            {
                **pagination,
                "results": AdminStoreListSerializer(page_qs, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class AdminStoreVisibilityUpdateAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def patch(self, request, pk):
        store = get_object_or_404(Store, pk=pk)
        serializer = AdminStoreVisibilitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        is_active = serializer.validated_data["is_active"]
        store.is_active = is_active
        store.save(update_fields=["is_active"])
        return Response(
            {
                "message": "Store visibility updated.",
                "store": AdminStoreListSerializer(
                    _admin_stores_queryset().get(pk=store.pk)
                ).data,
            },
            status=status.HTTP_200_OK,
        )


class AdminStoreReorderAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def patch(self, request):
        serializer = AdminStoreReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order_ids: Optional[List[int]] = serializer.validated_data.get("order")
        store_rows = serializer.validated_data.get("stores")

        if order_ids is not None:
            updates = {sid: index for index, sid in enumerate(order_ids)}
            store_ids = list(updates.keys())
        else:
            updates = {row["id"]: row["sort_order"] for row in store_rows}
            store_ids = list(updates.keys())

        existing_ids = set(
            Store.objects.filter(pk__in=store_ids).values_list("pk", flat=True)
        )
        missing = sorted(set(store_ids) - existing_ids)
        if missing:
            return Response(
                {"detail": f"Unknown store id(s): {', '.join(str(i) for i in missing)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            for store_id, sort_order in updates.items():
                Store.objects.filter(pk=store_id).update(sort_order=sort_order)

        qs = _admin_stores_queryset().filter(pk__in=store_ids).order_by("sort_order", "name")
        return Response(
            {
                "message": "Store order updated.",
                "results": AdminStoreListSerializer(qs, many=True).data,
            },
            status=status.HTTP_200_OK,
        )
