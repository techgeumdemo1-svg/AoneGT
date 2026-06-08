from typing import Optional, Tuple

from django.db.models import Q
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AdminActivityLog
from .orders import _apply_order_date_filter, _paginate_queryset
from .views import IsStaffUser


def _actor_display_name(user) -> str:
    if not user:
        return ""
    parts = [user.first_name or "", user.last_name or ""]
    return " ".join(p for p in parts if p).strip() or user.email


class AdminActivityLogListSerializer(serializers.ModelSerializer):
    activity_id = serializers.IntegerField(source="id", read_only=True)
    actor_id = serializers.IntegerField(read_only=True, allow_null=True)
    actor_name = serializers.SerializerMethodField()
    category_label = serializers.SerializerMethodField()

    class Meta:
        model = AdminActivityLog
        fields = (
            "activity_id",
            "actor_id",
            "actor_email",
            "actor_name",
            "category",
            "category_label",
            "action",
            "message",
            "target_type",
            "target_id",
            "metadata",
            "created_at",
        )

    def get_actor_name(self, obj):
        if obj.actor_id:
            return _actor_display_name(obj.actor)
        return obj.actor_email or ""

    def get_category_label(self, obj):
        return obj.get_category_display()


def _apply_activity_log_filters(queryset, request) -> Tuple[object, Optional[str], Optional[dict]]:
    category = (request.query_params.get("category") or "").strip().lower()
    if category:
        valid = {choice.value for choice in AdminActivityLog.Category}
        if category in valid:
            queryset = queryset.filter(category=category)

    action = (request.query_params.get("action") or "").strip()
    if action:
        queryset = queryset.filter(action__iexact=action)

    actor_id = (request.query_params.get("actor_id") or "").strip()
    if actor_id.isdigit():
        queryset = queryset.filter(actor_id=int(actor_id))

    target_type = (request.query_params.get("target_type") or "").strip().lower()
    if target_type:
        queryset = queryset.filter(target_type=target_type)

    target_id = (request.query_params.get("target_id") or "").strip()
    if target_id.isdigit():
        queryset = queryset.filter(target_id=int(target_id))

    search = (request.query_params.get("search") or "").strip()
    if search:
        q = (
            Q(message__icontains=search)
            | Q(actor_email__icontains=search)
            | Q(action__icontains=search)
            | Q(target_type__icontains=search)
        )
        if search.isdigit():
            q |= Q(pk=int(search)) | Q(target_id=int(search)) | Q(actor_id=int(search))
        queryset = queryset.filter(q)

    return _apply_order_date_filter(queryset, request)


class AdminActivityLogListAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        qs, date_err, date_filter = _apply_activity_log_filters(
            AdminActivityLog.objects.select_related("actor").order_by("-created_at"),
            request,
        )
        if date_err:
            return Response({"detail": date_err}, status=status.HTTP_400_BAD_REQUEST)

        page_qs, pagination = _paginate_queryset(qs, request)
        payload = {
            **pagination,
            "results": AdminActivityLogListSerializer(page_qs, many=True).data,
        }
        if date_filter:
            payload["date_filter"] = date_filter
        return Response(payload, status=status.HTTP_200_OK)
