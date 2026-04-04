from django.conf import settings
from django.contrib.auth import authenticate
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken
from .models import User, PasswordResetOTP
from .services.zoho_registration_gate import (
    ZohoContactCheckError,
    registration_email_check_configured,
    registration_email_exists_in_zoho,
)


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=6)

    class Meta:
        model = User
        fields = ['id', 'first_name', 'last_name', 'email', 'password']
        read_only_fields = ['id']

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
                    src = getattr(settings, 'REGISTER_ZOHO_EMAIL_SOURCE', 'inventory')
                    if src == 'commerce_salesorders':
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

    def create(self, validated_data):
        password = validated_data.pop('password')
        return User.objects.create_user(password=password, **validated_data)


class EmailCheckSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.lower()


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
    new_password = serializers.CharField(write_only=True, min_length=6)
    confirm_password = serializers.CharField(write_only=True, min_length=6)

    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})
        return attrs


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'first_name', 'last_name', 'email', 'created_at']
