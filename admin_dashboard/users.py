from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .activity_log_utils import record_admin_activity
from .models import AdminActivityLog, AdminRole
from .orders import _paginate_queryset
from .roles import assign_role_to_user
from .views import IsStaffUser

User = get_user_model()


def _admin_users_queryset():
    return User.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).select_related("admin_role_binding__role")


def _admin_user_display_name(user) -> str:
    parts = [user.first_name or "", user.last_name or ""]
    return " ".join(p for p in parts if p).strip() or user.email


def _admin_user_status_label(user) -> str:
    return "active" if user.is_active else "inactive"


class AdminUserListSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source="id", read_only=True)
    name = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "user_id",
            "first_name",
            "last_name",
            "name",
            "email",
            "phone",
            "is_staff",
            "is_superuser",
            "is_active",
            "admin_mfa_enabled",
            "role",
            "status",
            "last_login",
            "created_at",
        )
        read_only_fields = fields

    def get_name(self, obj):
        return _admin_user_display_name(obj)

    def get_status(self, obj):
        return _admin_user_status_label(obj)

    def get_role(self, obj):
        binding = getattr(obj, "admin_role_binding", None)
        if not binding or not binding.role_id:
            return None
        return {"id": binding.role_id, "name": binding.role.name}


class AdminUserCreateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    is_staff = serializers.BooleanField(required=False, default=True)
    admin_mfa_enabled = serializers.BooleanField(required=False, default=False)
    role_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_email(self, value):
        email = (value or "").strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("This email is already registered.")
        return email

    def validate_password(self, value):
        password = value or ""
        if any(ch.isspace() for ch in password):
            raise serializers.ValidationError("Password cannot contain spaces.")
        try:
            validate_password(password)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return password

    def validate_role_id(self, value):
        if value is None:
            return None
        if not AdminRole.objects.filter(pk=value).exists():
            raise serializers.ValidationError("Role not found.")
        return value

    def create(self, validated_data):
        role_id = validated_data.pop("role_id", None)
        password = validated_data.pop("password")
        is_staff = validated_data.pop("is_staff", True)
        user = User.objects.create_user(
            password=password,
            is_staff=is_staff,
            is_active=True,
            **validated_data,
        )
        role = AdminRole.objects.filter(pk=role_id).first() if role_id else None
        assign_role_to_user(user=user, role=role)
        return user


class AdminUserUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField(required=False)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, required=False)
    is_staff = serializers.BooleanField(required=False)
    admin_mfa_enabled = serializers.BooleanField(required=False)
    role_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_email(self, value):
        email = (value or "").strip().lower()
        user = self.context.get("user")
        if (
            user
            and User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists()
        ):
            raise serializers.ValidationError("This email is already registered.")
        return email

    def validate_password(self, value):
        if value is None:
            return value
        password = value or ""
        if any(ch.isspace() for ch in password):
            raise serializers.ValidationError("Password cannot contain spaces.")
        try:
            validate_password(password)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return password

    def validate_role_id(self, value):
        if value is None:
            return None
        if not AdminRole.objects.filter(pk=value).exists():
            raise serializers.ValidationError("Role not found.")
        return value

    def update(self, instance, validated_data):
        role_id = validated_data.pop("role_id", None) if "role_id" in validated_data else ...
        password = validated_data.pop("password", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if password:
            instance.set_password(password)
        instance.save()
        if role_id is not ...:
            role = AdminRole.objects.filter(pk=role_id).first() if role_id else None
            assign_role_to_user(user=instance, role=role)
        return instance


def _apply_admin_user_list_filters(queryset, request):
    is_active = (request.query_params.get("is_active") or "").strip().lower()
    if is_active in ("true", "1", "yes"):
        queryset = queryset.filter(is_active=True)
    elif is_active in ("false", "0", "no"):
        queryset = queryset.filter(is_active=False)

    status_filter = (request.query_params.get("status") or "").strip().lower()
    if status_filter == "active":
        queryset = queryset.filter(is_active=True)
    elif status_filter in ("inactive", "blocked"):
        queryset = queryset.filter(is_active=False)

    is_staff = (request.query_params.get("is_staff") or "").strip().lower()
    if is_staff in ("true", "1", "yes"):
        queryset = queryset.filter(is_staff=True)
    elif is_staff in ("false", "0", "no"):
        queryset = queryset.filter(is_staff=False)

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

    return queryset


def _admin_user_payload(user) -> dict:
    return AdminUserListSerializer(user).data


def _parse_user_id_query_param(request, *, required=True, path_pk=None):
    user_id = (request.query_params.get('id') or request.query_params.get('user_id') or '').strip()
    if not user_id and path_pk is not None:
        user_id = str(path_pk).strip()
    if not user_id and hasattr(request, 'data') and request.data is not None:
        body_id = request.data.get('id') or request.data.get('user_id')
        if body_id is not None and str(body_id).strip() != '':
            user_id = str(body_id).strip()
    if not user_id:
        if required:
            return None, Response(
                {
                    'detail': (
                        'User id is required. Use ?id=<id>, path /users/<id>/, '
                        'or include id in the request body.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None, None
    if not str(user_id).isdigit():
        return None, Response(
            {
                'detail': (
                    'User id must be a positive integer. Use ?id=<id> or /users/<id>/.'
                ),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    return int(user_id), None


def _admin_user_detail_response(user) -> Response:
    return Response(_admin_user_payload(user), status=status.HTTP_200_OK)


def _update_admin_user(request, user_id: int):
    user = get_object_or_404(_admin_users_queryset(), pk=user_id)
    serializer = AdminUserUpdateSerializer(
        data=request.data,
        partial=True,
        context={'user': user},
    )
    serializer.is_valid(raise_exception=True)

    if user.pk == request.user.pk:
        if 'is_staff' in serializer.validated_data and not serializer.validated_data['is_staff']:
            return Response(
                {'detail': 'You cannot remove your own staff access.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    serializer.update(user, serializer.validated_data)
    user.refresh_from_db()
    record_admin_activity(
        request,
        category=AdminActivityLog.Category.USERS,
        action='user.updated',
        message=f'Updated admin user #{user.pk} ({user.email}).',
        target_type='user',
        target_id=user.pk,
        metadata={'fields': sorted(serializer.validated_data.keys())},
    )
    return Response(
        {
            'message': 'Admin user updated.',
            'user': _admin_user_payload(user),
        },
        status=status.HTTP_200_OK,
    )


class AdminUserListCreateAPIView(APIView):
    """
    GET   /api/admin/users/          — list
    GET   /api/admin/users/?id=<id>  — single admin user detail
    POST  /api/admin/users/          — create
    PATCH /api/admin/users/?id=<id>  — update (query param)
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        if (request.query_params.get('id') or request.query_params.get('user_id') or '').strip():
            user_id, err = _parse_user_id_query_param(request)
            if err:
                return err
            user = get_object_or_404(_admin_users_queryset(), pk=user_id)
            return _admin_user_detail_response(user)

        qs = _apply_admin_user_list_filters(
            _admin_users_queryset().select_related("admin_role_binding__role").order_by("-created_at"),
            request,
        )
        page_qs, pagination = _paginate_queryset(qs, request)
        return Response(
            {
                **pagination,
                "results": AdminUserListSerializer(page_qs, many=True).data,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        serializer = AdminUserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        record_admin_activity(
            request,
            category=AdminActivityLog.Category.USERS,
            action="user.created",
            message=f"Created admin user #{user.pk} ({user.email}).",
            target_type="user",
            target_id=user.pk,
        )
        return Response(
            {
                "message": "Admin user created.",
                "user": _admin_user_payload(user),
            },
            status=status.HTTP_201_CREATED,
        )

    def patch(self, request):
        user_id, err = _parse_user_id_query_param(request)
        if err:
            return err
        return _update_admin_user(request, user_id)


class AdminUserDetailUpdateAPIView(APIView):
    """
    GET   /api/admin/users/<id>/ — admin user detail
    PATCH /api/admin/users/<id>/ — update admin user
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request, pk):
        user = get_object_or_404(_admin_users_queryset(), pk=pk)
        return _admin_user_detail_response(user)

    def patch(self, request, pk):
        return _update_admin_user(request, pk)


class AdminUserDeactivateAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def patch(self, request):
        user_id, err = _parse_user_id_query_param(request)
        if err:
            return err
        user = get_object_or_404(_admin_users_queryset(), pk=user_id)
        if user.pk == request.user.pk:
            return Response(
                {"detail": "You cannot deactivate your own account."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not user.is_active:
            return Response(
                {
                    "message": "Admin user is already inactive.",
                    "user_id": user.pk,
                    "status": _admin_user_status_label(user),
                    "is_active": user.is_active,
                },
                status=status.HTTP_200_OK,
            )
        user.is_active = False
        user.save(update_fields=["is_active"])
        record_admin_activity(
            request,
            category=AdminActivityLog.Category.USERS,
            action="user.deactivated",
            message=f"Deactivated admin user #{user.pk} ({user.email}).",
            target_type="user",
            target_id=user.pk,
        )
        return Response(
            {
                "message": "Admin user deactivated.",
                "user_id": user.pk,
                "status": _admin_user_status_label(user),
                "is_active": user.is_active,
            },
            status=status.HTTP_200_OK,
        )


class AdminUserReactivateAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def patch(self, request):
        user_id, err = _parse_user_id_query_param(request)
        if err:
            return err
        user = get_object_or_404(_admin_users_queryset(), pk=user_id)
        if user.is_active:
            return Response(
                {
                    "message": "Admin user is already active.",
                    "user_id": user.pk,
                    "status": _admin_user_status_label(user),
                    "is_active": user.is_active,
                },
                status=status.HTTP_200_OK,
            )
        user.is_active = True
        user.save(update_fields=["is_active"])
        record_admin_activity(
            request,
            category=AdminActivityLog.Category.USERS,
            action="user.reactivated",
            message=f"Reactivated admin user #{user.pk} ({user.email}).",
            target_type="user",
            target_id=user.pk,
        )
        return Response(
            {
                "message": "Admin user reactivated.",
                "user_id": user.pk,
                "status": _admin_user_status_label(user),
                "is_active": user.is_active,
            },
            status=status.HTTP_200_OK,
        )
