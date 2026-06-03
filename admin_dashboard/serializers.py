from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from .models import AdminLoginOTP

User = get_user_model()


def build_admin_login_response(user):
    refresh = RefreshToken.for_user(user)
    return {
        "user": {
            "id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
        },
        "tokens": {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        },
    }


def _authenticate_admin(email, password):
    user = authenticate(username=email, password=password)
    if not user:
        raise serializers.ValidationError({"detail": "Invalid email or password."})
    if not user.is_active:
        raise serializers.ValidationError({"detail": "Your account is inactive."})
    if not (user.is_staff or user.is_superuser):
        raise serializers.ValidationError({"detail": "Admin access required."})
    return user


class AdminLoginSerializer(serializers.Serializer):
    """Step 1: validate email/password (OTP is sent by the view)."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        email = (attrs.get("email") or "").strip().lower()
        attrs["user"] = _authenticate_admin(email, attrs.get("password"))
        attrs["email"] = email
        return attrs


class AdminLoginVerifyOTPSerializer(serializers.Serializer):
    """Step 2: verify email OTP and complete login."""

    email = serializers.EmailField()
    otp = serializers.CharField(max_length=6, min_length=6)

    def validate_email(self, value):
        return (value or "").strip().lower()

    def validate_otp(self, value):
        otp = str(value).strip()
        if not otp.isdigit() or len(otp) != 6:
            raise serializers.ValidationError("Enter a valid 6-digit OTP.")
        return otp

    def validate(self, attrs):
        email = attrs["email"]
        otp_code = attrs["otp"]
        user = User.objects.filter(email__iexact=email).first()
        otp = None
        if (
            user
            and user.is_active
            and (user.is_staff or user.is_superuser)
        ):
            otp = AdminLoginOTP.objects.filter(
                user=user,
                otp_code=otp_code,
                is_used=False,
            ).first()
        if not user or not otp or otp.is_expired:
            raise serializers.ValidationError({"detail": "Invalid or expired login OTP."})
        attrs["user"] = user
        attrs["otp_row"] = otp
        return attrs


class AdminLogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()

    def validate_refresh(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Refresh token is required.")
        return value


class AdminForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return (value or "").strip().lower()


class AdminResetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(max_length=6, min_length=6)
    new_password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        return (value or "").strip().lower()

    def validate_otp(self, value):
        otp = str(value).strip()
        if not otp.isdigit() or len(otp) != 6:
            raise serializers.ValidationError("Enter a valid 6-digit OTP.")
        return otp

    def validate(self, attrs):
        password = attrs["new_password"]
        if any(ch.isspace() for ch in password):
            raise serializers.ValidationError({"new_password": "Password cannot contain spaces."})
        try:
            validate_password(password)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)})
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return attrs


class AdminMeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "first_name",
            "last_name",
            "email",
            "is_staff",
            "is_superuser",
            "is_active",
            "created_at",
        ]
        read_only_fields = fields
