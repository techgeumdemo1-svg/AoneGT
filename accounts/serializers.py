from django.conf import settings
from django.contrib.auth import authenticate
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken
from .models import User, PasswordResetOTP, RegistrationOTP
from .services.zoho_registration_gate import (
    ZohoContactCheckError,
    registration_email_check_configured,
    registration_email_exists_in_zoho,
    resolved_register_zoho_email_source,
)


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    phone = serializers.CharField(max_length=32, required=True)
    registration_otp = serializers.CharField(
        write_only=True,
        max_length=6,
        min_length=6,
        required=False,
        allow_blank=True,
    )

    class Meta:
        model = User
        fields = [
            'id', 'first_name', 'last_name', 'email', 'phone', 'password', 'registration_otp',
        ]
        read_only_fields = ['id']

    def validate_phone(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Phone number is required.')
        return value

    def validate_email(self, value):
        normalized = value.strip().lower()
        if User.objects.filter(email__iexact=normalized).exists():
            raise serializers.ValidationError('This email is already registered.')

        if getattr(settings, 'REGISTER_REQUIRE_ZOHO_CONTACT', False):
            if not registration_email_check_configured():
                raise serializers.ValidationError(
                    'Registration requires Zoho email verification, but the server is not '
                    'configured. Set ZOHO_ACCESS_TOKEN and the org id for your chosen '
                    'REGISTER_ZOHO_EMAIL_SOURCE (Inventory or Commerce).',
                )
            try:
                if not registration_email_exists_in_zoho(normalized):
                    if resolved_register_zoho_email_source() == 'commerce_salesorders':
                        msg = (
                            'This email has no sales orders in Zoho Commerce yet, or it does '
                            'not match. Use the email from your store orders.'
                        )
                    else:
                        msg = (
                            'This email is not found as a customer in Zoho Inventory. '
                            'Use the same email you use with our store.'
                        )
                    raise serializers.ValidationError(msg)
            except ZohoContactCheckError as e:
                raise serializers.ValidationError(
                    f'Could not verify email with Zoho: {e}',
                ) from e

        return normalized

    def validate(self, attrs):
        email = attrs['email']
        require_otp = getattr(settings, 'REGISTER_REQUIRE_EMAIL_OTP', False)
        otp = attrs.pop('registration_otp', None) or ''
        otp = str(otp).strip()
        if require_otp:
            if len(otp) != 6 or not otp.isdigit():
                raise serializers.ValidationError(
                    {'registration_otp': 'Enter the 6-digit code sent to your email.'},
                )
            row = (
                RegistrationOTP.objects.filter(
                    email__iexact=email,
                    otp_code=otp,
                    is_used=False,
                )
                .order_by('-created_at')
                .first()
            )
            if not row or row.is_expired:
                raise serializers.ValidationError(
                    {'registration_otp': 'Invalid or expired verification code.'},
                )
            self._registration_otp_row = row
        elif otp:
            raise serializers.ValidationError(
                {'registration_otp': 'Email verification code is not required for registration.'},
            )
        return attrs

    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User.objects.create_user(password=password, **validated_data)
        row = getattr(self, '_registration_otp_row', None)
        if row:
            row.is_used = True
            row.save(update_fields=['is_used'])
        return user


class EmailCheckSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.lower()


class RequestRegistrationOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.strip().lower()


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        email = attrs.get('email', '').lower()
        password = attrs.get('password')
        user = authenticate(username=email, password=password)
        if not user:
            raise serializers.ValidationError({'detail': 'Invalid email or password.'})
        if not user.is_active:
            raise serializers.ValidationError({'detail': 'Your account is inactive.'})
        attrs['user'] = user
        return attrs

    def to_representation(self, instance):
        user = instance['user']
        refresh = RefreshToken.for_user(user)
        return {
            'user': {
                'id': user.id,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'email': user.email,
            },
            'tokens': {
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            }
        }


class ForgotPasswordRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.lower()


class ResetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(max_length=6)
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})
        return attrs


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'first_name', 'last_name', 'email', 'phone', 'created_at']
