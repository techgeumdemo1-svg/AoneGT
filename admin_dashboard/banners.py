from typing import List, Optional

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Banner
from catalog.serializers import BannerAdminSerializer

from .orders import _paginate_queryset
from .views import IsStaffUser


def _admin_banners_queryset():
    return Banner.objects.select_related("store").order_by("sort_order", "id")


class AdminBannerListSerializer(serializers.ModelSerializer):
    banner_id = serializers.IntegerField(source="id", read_only=True)
    store_id = serializers.IntegerField(read_only=True, allow_null=True)
    store_name = serializers.SerializerMethodField()

    class Meta:
        model = Banner
        fields = (
            "banner_id",
            "store_id",
            "store_name",
            "title",
            "subtitle",
            "image_url",
            "link_url",
            "sort_order",
            "is_active",
            "created_at",
            "updated_at",
        )

    def get_store_name(self, obj):
        return obj.store.name if obj.store_id else None


def _apply_banner_list_filters(queryset, request):
    is_active = (request.query_params.get("is_active") or "").strip().lower()
    if is_active in ("true", "1", "yes"):
        queryset = queryset.filter(is_active=True)
    elif is_active in ("false", "0", "no"):
        queryset = queryset.filter(is_active=False)

    store_id = (request.query_params.get("store_id") or "").strip()
    if store_id.lower() in ("null", "none", "global"):
        queryset = queryset.filter(store_id__isnull=True)
    elif store_id.isdigit():
        queryset = queryset.filter(store_id=int(store_id))

    search = (request.query_params.get("search") or "").strip()
    if search:
        q = (
            Q(title__icontains=search)
            | Q(subtitle__icontains=search)
            | Q(image_url__icontains=search)
            | Q(link_url__icontains=search)
        )
        if search.isdigit():
            q |= Q(pk=int(search))
        queryset = queryset.filter(q)

    return queryset


class AdminBannerReorderItemSerializer(serializers.Serializer):
    id = serializers.IntegerField(min_value=1)
    sort_order = serializers.IntegerField(min_value=0)


class AdminBannerReorderSerializer(serializers.Serializer):
    order = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=False,
    )
    banners = AdminBannerReorderItemSerializer(many=True, required=False)

    def validate(self, attrs):
        order = attrs.get("order")
        banners = attrs.get("banners")
        if not order and not banners:
            raise serializers.ValidationError(
                "Provide order (list of banner ids) or banners (id + sort_order)."
            )
        if order and banners:
            raise serializers.ValidationError(
                "Provide only one of order or banners, not both."
            )
        if order and len(order) != len(set(order)):
            raise serializers.ValidationError("order must not contain duplicate ids.")
        if banners:
            ids = [row["id"] for row in banners]
            if len(ids) != len(set(ids)):
                raise serializers.ValidationError("banners must not contain duplicate ids.")
        return attrs


def _banner_payload(banner: Banner) -> dict:
    return AdminBannerListSerializer(banner).data


def _parse_banner_id_query_param(request, *, required=True):
    banner_id = (request.query_params.get('id') or '').strip()
    if not banner_id:
        if required:
            return None, Response(
                {'detail': 'Query parameter id is required and must be a positive integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None, None
    if not banner_id.isdigit():
        return None, Response(
            {'detail': 'Query parameter id is required and must be a positive integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return int(banner_id), None


class AdminBannerListCreateAPIView(APIView):
    """
    GET    /api/admin/banners/           — list
    POST   /api/admin/banners/           — create
    PATCH  /api/admin/banners/?id=<id>  — update
    DELETE /api/admin/banners/?id=<id>  — delete
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        qs = _apply_banner_list_filters(_admin_banners_queryset(), request)
        page_qs, pagination = _paginate_queryset(qs, request)
        return Response(
            {
                **pagination,
                "results": AdminBannerListSerializer(page_qs, many=True).data,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        serializer = BannerAdminSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        banner = serializer.save()
        banner = _admin_banners_queryset().get(pk=banner.pk)
        return Response(
            {
                "message": "Banner created.",
                "banner": _banner_payload(banner),
            },
            status=status.HTTP_201_CREATED,
        )

    def patch(self, request):
        banner_id, err = _parse_banner_id_query_param(request)
        if err:
            return err
        banner = get_object_or_404(_admin_banners_queryset(), pk=banner_id)
        serializer = BannerAdminSerializer(banner, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        banner = _admin_banners_queryset().get(pk=banner_id)
        return Response(
            {
                "message": "Banner updated.",
                "banner": _banner_payload(banner),
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request):
        banner_id, err = _parse_banner_id_query_param(request)
        if err:
            return err
        banner = get_object_or_404(Banner, pk=banner_id)
        deleted_id = banner.pk
        banner.delete()
        return Response(
            {"message": "Banner deleted.", "banner_id": deleted_id},
            status=status.HTTP_200_OK,
        )


class AdminBannerDetailAPIView(AdminBannerListCreateAPIView):
    """Alias kept for backwards compatibility — use /api/admin/banners/?id=<id> instead."""


class AdminBannerReorderAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def patch(self, request):
        serializer = AdminBannerReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order_ids: Optional[List[int]] = serializer.validated_data.get("order")
        banner_rows = serializer.validated_data.get("banners")

        if order_ids is not None:
            updates = {bid: index for index, bid in enumerate(order_ids)}
            banner_ids = list(updates.keys())
        else:
            updates = {row["id"]: row["sort_order"] for row in banner_rows}
            banner_ids = list(updates.keys())

        existing_ids = set(
            Banner.objects.filter(pk__in=banner_ids).values_list("pk", flat=True)
        )
        missing = sorted(set(banner_ids) - existing_ids)
        if missing:
            return Response(
                {"detail": f"Unknown banner id(s): {', '.join(str(i) for i in missing)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            for banner_id, sort_order in updates.items():
                Banner.objects.filter(pk=banner_id).update(sort_order=sort_order)

        qs = _admin_banners_queryset().filter(pk__in=banner_ids)
        return Response(
            {
                "message": "Banner order updated.",
                "results": AdminBannerListSerializer(qs, many=True).data,
            },
            status=status.HTTP_200_OK,
        )
