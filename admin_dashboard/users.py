from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .orders import _paginate_queryset
from .views import IsStaffUser

User = get_user_model()


def _admin_users_queryset():
    return User.objects.filter(Q(is_staff=True) | Q(is_superuser=True))


def _admin_user_display_name(user) -> str:
    parts = [user.first_name or "", user.last_name or ""]
    return " ".join(p for p in parts if p).strip() or user.email


def _admin_user_status_label(user) -> str:
    return "active" if user.is_active else "inactive"


class AdminUserListSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source="id", read_only=True)
    name = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

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
            "status",
            "last_login",
            "created_at",
        )
        read_only_fields = fields

    def get_name(self, obj):
        return _admin_user_display_name(obj)

    def get_status(self, obj):
        return _admin_user_status_label(obj)


class AdminUserCreateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    is_staff = serializers.BooleanField(required=False, default=True)

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

    def create(self, validated_data):
        password = validated_data.pop("password")
        is_staff = validated_data.pop("is_staff", True)
        return User.objects.create_user(
            password=password,
            is_staff=is_staff,
            is_active=True,
            **validated_data,
        )


class AdminUserUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField(required=False)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, required=False)
    is_staff = serializers.BooleanField(required=False)

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

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if password:
            instance.set_password(password)
        instance.save()
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


class AdminUserListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        qs = _apply_admin_user_list_filters(
            _admin_users_queryset().order_by("-created_at"),
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
        return Response(
            {
                "message": "Admin user created.",
                "user": _admin_user_payload(user),
            },
            status=status.HTTP_201_CREATED,
        )


class AdminUserDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def patch(self, request, pk):
        user = get_object_or_404(_admin_users_queryset(), pk=pk)
        serializer = AdminUserUpdateSerializer(
            data=request.data,
            partial=True,
            context={"user": user},
        )
        serializer.is_valid(raise_exception=True)

        if user.pk == request.user.pk:
            if "is_staff" in serializer.validated_data and not serializer.validated_data["is_staff"]:
                return Response(
                    {"detail": "You cannot remove your own staff access."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        serializer.update(user, serializer.validated_data)
        user.refresh_from_db()
        return Response(
            {
                "message": "Admin user updated.",
                "user": _admin_user_payload(user),
            },
            status=status.HTTP_200_OK,
        )


class AdminUserDeactivateAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def patch(self, request, pk):
        user = get_object_or_404(_admin_users_queryset(), pk=pk)
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

    def patch(self, request, pk):
        user = get_object_or_404(_admin_users_queryset(), pk=pk)
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
        return Response(
            {
                "message": "Admin user reactivated.",
                "user_id": user.pk,
                "status": _admin_user_status_label(user),
                "is_active": user.is_active,
            },
            status=status.HTTP_200_OK,
        )
