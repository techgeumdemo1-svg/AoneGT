from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CMSPage, FAQ
from .orders import _paginate_queryset
from .views import IsStaffUser


class AdminFAQListSerializer(serializers.ModelSerializer):
    faq_id = serializers.IntegerField(source="id", read_only=True)

    class Meta:
        model = FAQ
        fields = (
            "faq_id",
            "question",
            "answer",
            "sort_order",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("faq_id", "created_at", "updated_at")


class AdminFAQWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = FAQ
        fields = ("question", "answer", "sort_order", "is_active")


class AdminCMSPageListSerializer(serializers.ModelSerializer):
    page_id = serializers.IntegerField(source="id", read_only=True)

    class Meta:
        model = CMSPage
        fields = (
            "page_id",
            "slug",
            "title",
            "content",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("page_id", "slug", "created_at", "updated_at")


class AdminCMSPageUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CMSPage
        fields = ("title", "content", "is_active")


def _apply_faq_list_filters(queryset, request):
    is_active = (request.query_params.get("is_active") or "").strip().lower()
    if is_active in ("true", "1", "yes"):
        queryset = queryset.filter(is_active=True)
    elif is_active in ("false", "0", "no"):
        queryset = queryset.filter(is_active=False)

    search = (request.query_params.get("search") or "").strip()
    if search:
        q = Q(question__icontains=search) | Q(answer__icontains=search)
        if search.isdigit():
            q |= Q(pk=int(search))
        queryset = queryset.filter(q)

    return queryset.order_by("sort_order", "id")


def _apply_cms_page_list_filters(queryset, request):
    is_active = (request.query_params.get("is_active") or "").strip().lower()
    if is_active in ("true", "1", "yes"):
        queryset = queryset.filter(is_active=True)
    elif is_active in ("false", "0", "no"):
        queryset = queryset.filter(is_active=False)

    slug = (request.query_params.get("slug") or "").strip()
    if slug:
        queryset = queryset.filter(slug=slug)

    search = (request.query_params.get("search") or "").strip()
    if search:
        q = Q(slug__icontains=search) | Q(title__icontains=search) | Q(content__icontains=search)
        if search.isdigit():
            q |= Q(pk=int(search))
        queryset = queryset.filter(q)

    return queryset.order_by("slug")


def _parse_faq_id_query_param(request, *, required=True):
    faq_id = (request.query_params.get('id') or '').strip()
    if not faq_id:
        if required:
            return None, Response(
                {'detail': 'Query parameter id is required and must be a positive integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None, None
    if not faq_id.isdigit():
        return None, Response(
            {'detail': 'Query parameter id is required and must be a positive integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return int(faq_id), None


def _parse_cms_page_id_query_param(request, *, required=True):
    page_id = (request.query_params.get('id') or '').strip()
    if not page_id:
        if required:
            return None, Response(
                {'detail': 'Query parameter id is required and must be a positive integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None, None
    if not page_id.isdigit():
        return None, Response(
            {'detail': 'Query parameter id is required and must be a positive integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return int(page_id), None


class AdminFAQListCreateAPIView(APIView):
    """
    GET    /api/admin/cms/faqs/          — list
    POST   /api/admin/cms/faqs/          — create
    PATCH  /api/admin/cms/faqs/?id=<id> — update
    DELETE /api/admin/cms/faqs/?id=<id> — delete
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        qs = _apply_faq_list_filters(FAQ.objects.all(), request)
        page_qs, pagination = _paginate_queryset(qs, request)
        return Response(
            {
                **pagination,
                "results": AdminFAQListSerializer(page_qs, many=True).data,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        serializer = AdminFAQWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        faq = serializer.save()
        return Response(
            {
                "message": "FAQ created.",
                "faq": AdminFAQListSerializer(faq).data,
            },
            status=status.HTTP_201_CREATED,
        )

    def patch(self, request):
        faq_id, err = _parse_faq_id_query_param(request)
        if err:
            return err
        faq = get_object_or_404(FAQ, pk=faq_id)
        serializer = AdminFAQWriteSerializer(faq, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        faq = serializer.save()
        return Response(
            {
                "message": "FAQ updated.",
                "faq": AdminFAQListSerializer(faq).data,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request):
        faq_id, err = _parse_faq_id_query_param(request)
        if err:
            return err
        faq = get_object_or_404(FAQ, pk=faq_id)
        deleted_id = faq.pk
        faq.delete()
        return Response(
            {"message": "FAQ deleted.", "faq_id": deleted_id},
            status=status.HTTP_200_OK,
        )


class AdminCMSPageListAPIView(APIView):
    """
    GET   /api/admin/cms/pages/          — list
    PATCH /api/admin/cms/pages/?id=<id> — update
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        qs = _apply_cms_page_list_filters(CMSPage.objects.all(), request)
        page_qs, pagination = _paginate_queryset(qs, request)
        return Response(
            {
                **pagination,
                "results": AdminCMSPageListSerializer(page_qs, many=True).data,
            },
            status=status.HTTP_200_OK,
        )

    def patch(self, request):
        page_id, err = _parse_cms_page_id_query_param(request)
        if err:
            return err
        page = get_object_or_404(CMSPage, pk=page_id)
        serializer = AdminCMSPageUpdateSerializer(page, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        page = serializer.save()
        return Response(
            {
                "message": "Page updated.",
                "page": AdminCMSPageListSerializer(page).data,
            },
            status=status.HTTP_200_OK,
        )
