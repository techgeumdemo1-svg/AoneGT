from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .activity_log_utils import record_admin_activity
from .models import AdminActivityLog, AdminPermission, AdminRole, AdminUserRole
from .views import IsStaffUser

class IsSuperAdmin(IsStaffUser):
    message = "Only super admin can manage roles."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        return bool(request.user and request.user.is_superuser)


class AdminRoleSerializer(serializers.ModelSerializer):
    permission_codes = serializers.ListField(
        child=serializers.CharField(max_length=64),
        required=False,
        allow_empty=True,
        write_only=True,
    )
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = AdminRole
        fields = ("id", "name", "is_system", "permissions", "permission_codes", "created_at", "updated_at")
        read_only_fields = ("id", "is_system", "permissions", "created_at", "updated_at")

    def get_permissions(self, obj):
        return list(
            obj.permissions.order_by("module", "code").values("id", "code", "name", "module", "description")
        )

    def validate_permission_codes(self, value):
        codes = sorted({(v or "").strip() for v in value if (v or "").strip()})
        missing = [
            code for code in codes if not AdminPermission.objects.filter(code=code).exists()
        ]
        if missing:
            raise serializers.ValidationError(f"Invalid permission code(s): {', '.join(missing)}")
        return codes


class AdminRoleListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        roles = AdminRole.objects.prefetch_related("permissions").order_by("name")
        return Response(AdminRoleSerializer(roles, many=True).data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = AdminRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = AdminRole.objects.create(name=serializer.validated_data["name"], is_system=False)
        codes = serializer.validated_data.get("permission_codes") or []
        if codes:
            role.permissions.set(AdminPermission.objects.filter(code__in=codes))
        record_admin_activity(
            request,
            category=AdminActivityLog.Category.USERS,
            action="role.created",
            message=f"Created role #{role.pk} ({role.name}).",
            target_type="role",
            target_id=role.pk,
            metadata={"permission_codes": codes},
        )
        return Response(
            {"message": "Role created.", "role": AdminRoleSerializer(role).data},
            status=status.HTTP_201_CREATED,
        )


class AdminRoleDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def patch(self, request):
        role_id = (request.query_params.get("id") or request.data.get("id") or "").strip()
        if not role_id.isdigit():
            return Response({"detail": "Role id is required. Use ?id=<id>."}, status=status.HTTP_400_BAD_REQUEST)
        role = get_object_or_404(AdminRole.objects.prefetch_related("permissions"), pk=int(role_id))
        if role.is_system:
            return Response({"detail": "System roles cannot be edited."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = AdminRoleSerializer(role, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        if "name" in serializer.validated_data:
            role.name = serializer.validated_data["name"]
            role.save(update_fields=["name", "updated_at"])
        if "permission_codes" in serializer.validated_data:
            codes = serializer.validated_data.get("permission_codes") or []
            role.permissions.set(AdminPermission.objects.filter(code__in=codes))
        role.refresh_from_db()
        record_admin_activity(
            request,
            category=AdminActivityLog.Category.USERS,
            action="role.updated",
            message=f"Updated role #{role.pk} ({role.name}).",
            target_type="role",
            target_id=role.pk,
        )
        return Response({"message": "Role updated.", "role": AdminRoleSerializer(role).data}, status=status.HTTP_200_OK)


class AdminPermissionListAPIView(APIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        items = list(AdminPermission.objects.order_by("module", "code").values("id", "code", "name", "module", "description"))
        return Response({"results": items}, status=status.HTTP_200_OK)


def assign_role_to_user(*, user, role):
    if role is None:
        AdminUserRole.objects.filter(user=user).delete()
        return
    AdminUserRole.objects.update_or_create(user=user, defaults={"role": role})
