# AoneGT Full Project Source Code

## Project Directory Structure

`	ext
AoneGT/
├── .env
├── .env.example
├── .gitignore
├── FULL_CODE_README.md
├── README.md
├── accounts
│   ├── __init__.py
│   ├── admin.py
│   ├── apps.py
│   ├── models.py
│   ├── serializers.py
│   ├── services
│   │   ├── __init__.py
│   │   ├── zoho_commerce_contact.py
│   │   ├── zoho_inventory_contact.py
│   │   └── zoho_registration_gate.py
│   ├── urls.py
│   └── views.py
├── aonegt
│   ├── __init__.py
│   ├── asgi.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── catalog
│   ├── __init__.py
│   ├── admin.py
│   ├── apps.py
│   ├── management
│   │   ├── __init__.py
│   │   └── commands
│   │       ├── __init__.py
│   │       └── sync_zoho_products.py
│   ├── models.py
│   ├── serializers.py
│   ├── services
│   │   ├── __init__.py
│   │   ├── zoho_commerce_products.py
│   │   ├── zoho_product_sync.py
│   │   └── zoho_sites.py
│   ├── urls.py
│   └── views.py
├── manage.py
├── offers
│   ├── __init__.py
│   ├── admin.py
│   ├── apps.py
│   ├── models.py
│   ├── serializers.py
│   ├── services.py
│   ├── tests.py
│   ├── urls.py
│   └── views.py
├── requirements.txt
├── shop
│   ├── __init__.py
│   ├── admin.py
│   ├── apps.py
│   ├── models.py
│   ├── serializers.py
│   ├── services
│   │   ├── __init__.py
│   │   ├── cart_zoho.py
│   │   ├── order_sync_state.py
│   │   ├── zoho_commerce.py
│   │   └── zoho_returns.py
│   ├── urls.py
│   └── views.py
└── zoho_integration
    ├── __init__.py
    ├── admin.py
    ├── apps.py
    ├── models.py
    ├── services.py
    ├── urls.py
    └── views.py
`

## manage.py

`python
#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aonegt.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and available on your "
            "PYTHONPATH environment variable? Did you forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()

`

## accounts\admin.py

`python
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, PasswordResetOTP, RegistrationOTP


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ('id',)
    list_display = ('id', 'email', 'phone', 'first_name', 'last_name', 'is_staff', 'is_active')
    search_fields = ('email', 'phone', 'first_name', 'last_name')
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'phone')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login',)}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'email', 'first_name', 'last_name', 'phone',
                'password1', 'password2', 'is_staff', 'is_active',
            )
        }),
    )


@admin.register(PasswordResetOTP)
class PasswordResetOTPAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'otp_code', 'is_used', 'created_at', 'expires_at')
    search_fields = ('user__email', 'otp_code')


@admin.register(RegistrationOTP)
class RegistrationOTPAdmin(admin.ModelAdmin):
    list_display = ('id', 'email', 'otp_code', 'is_used', 'created_at', 'expires_at')
    search_fields = ('email', 'otp_code')

`

## accounts\apps.py

`python
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

`

## accounts\models.py

`python
from datetime import timedelta
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.db import models
from django.utils import timezone
import random


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name']

    objects = UserManager()

    def __str__(self):
        return self.email


class PasswordResetOTP(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_reset_otps')
    otp_code = models.CharField(max_length=6)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.otp_code:
            self.otp_code = f"{random.randint(100000, 999999)}"
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(minutes=10)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at


class RegistrationOTP(models.Model):
    """One-time code sent to email before account creation (no User row yet)."""

    email = models.EmailField(db_index=True)
    otp_code = models.CharField(max_length=6)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        self.email = (self.email or '').strip().lower()
        if not self.otp_code:
            self.otp_code = f'{random.randint(100000, 999999)}'
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(minutes=10)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f'{self.email} ({self.otp_code})'

`

## accounts\serializers.py

`python
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

`

## accounts\urls.py

`python
from django.urls import path
from .views import (
    RegisterAPIView,
    CheckEmailAPIView,
    CheckZohoContactAPIView,
    RequestRegistrationOTPAPIView,
    LoginAPIView,
    ForgotPasswordAPIView,
    ResetPasswordAPIView,
    ProfileAPIView,
)

urlpatterns = [
    path('register/', RegisterAPIView.as_view(), name='register'),
    path('check-email/', CheckEmailAPIView.as_view(), name='check-email'),
    path('check-zoho-contact/', CheckZohoContactAPIView.as_view(), name='check-zoho-contact'),
    path(
        'request-registration-code/',
        RequestRegistrationOTPAPIView.as_view(),
        name='request-registration-code',
    ),
    path('login/', LoginAPIView.as_view(), name='login'),
    path('forgot-password/', ForgotPasswordAPIView.as_view(), name='forgot-password'),
    path('reset-password/', ResetPasswordAPIView.as_view(), name='reset-password'),
    path('profile/', ProfileAPIView.as_view(), name='profile'),
]

`

## accounts\views.py

`python
from django.conf import settings
from django.core.mail import send_mail
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import User, PasswordResetOTP, RegistrationOTP
from .serializers import (
    RegisterSerializer,
    EmailCheckSerializer,
    RequestRegistrationOTPSerializer,
    LoginSerializer,
    ForgotPasswordRequestSerializer,
    ResetPasswordSerializer,
    UserProfileSerializer,
)
from .services.zoho_registration_gate import (
    ZohoContactCheckError,
    registration_email_check_configured,
    registration_email_exists_in_zoho,
    resolved_register_zoho_email_source,
)


class RegisterAPIView(APIView):
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {
                'message': 'Account created successfully.',
                'user': UserProfileSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class CheckEmailAPIView(APIView):
    def post(self, request):
        serializer = EmailCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        exists = User.objects.filter(email__iexact=email).exists()
        return Response(
            {
                'email': email,
                'exists': exists,
                'message': 'Use this result to continue sign-in or registration.',
            },
            status=status.HTTP_200_OK,
        )


class CheckZohoContactAPIView(APIView):
    """
    Optional UX step before register: whether the email is allowed under the active Zoho source
    (Inventory contacts vs Commerce sales orders). When REGISTER_REQUIRE_ZOHO_CONTACT is False,
    returns exists_in_zoho: null.
    """

    def post(self, request):
        serializer = EmailCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        if not getattr(settings, 'REGISTER_REQUIRE_ZOHO_CONTACT', False):
            return Response(
                {
                    'email': email,
                    'zoho_check_required': False,
                    'exists_in_zoho': None,
                    'message': 'Zoho contact check is disabled (REGISTER_REQUIRE_ZOHO_CONTACT).',
                },
                status=status.HTTP_200_OK,
            )

        if not registration_email_check_configured():
            return Response(
                {
                    'detail': 'Zoho is not configured for registration checks. Set '
                    'ZOHO_ACCESS_TOKEN and the organization id for your '
                    'REGISTER_ZOHO_EMAIL_SOURCE.',
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            exists = registration_email_exists_in_zoho(email)
        except ZohoContactCheckError as e:
            return Response(
                {'detail': str(e)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        src = resolved_register_zoho_email_source()
        return Response(
            {
                'email': email,
                'zoho_check_required': True,
                'source': src,
                'exists_in_zoho': exists,
                'message': (
                    'Email matches Zoho records. You can register.'
                    if exists
                    else (
                        'No matching Commerce sales orders for this email.'
                        if src == 'commerce_salesorders'
                        else 'Email not found in Zoho Inventory contacts.'
                    )
                ),
            },
            status=status.HTTP_200_OK,
        )


class RequestRegistrationOTPAPIView(APIView):
    """
    Sends a 6-digit code to the email for signup when REGISTER_REQUIRE_EMAIL_OTP is True.
    Uses the same generic response whether the email is ineligible, to limit enumeration.
    """

    def post(self, request):
        serializer = RequestRegistrationOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        generic = {
            'message': (
                'If this email is eligible for registration, a verification code has been sent.'
            ),
            'email': email,
        }

        if User.objects.filter(email__iexact=email).exists():
            return Response(generic, status=status.HTTP_200_OK)

        if getattr(settings, 'REGISTER_REQUIRE_ZOHO_CONTACT', False):
            if not registration_email_check_configured():
                return Response(
                    {
                        'detail': (
                            'Zoho is not configured for registration checks. Set '
                            'ZOHO_ACCESS_TOKEN and the organization id for your '
                            'REGISTER_ZOHO_EMAIL_SOURCE.'
                        ),
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            try:
                if not registration_email_exists_in_zoho(email):
                    return Response(generic, status=status.HTTP_200_OK)
            except ZohoContactCheckError as e:
                return Response({'detail': str(e)}, status=status.HTTP_502_BAD_GATEWAY)

        otp = RegistrationOTP.objects.create(email=email)
        subject = 'AoneGt registration verification code'
        message = (
            f'Your registration verification code is: {otp.otp_code}\n'
            f'This code expires in 10 minutes.\n\n'
            f'If you did not request this, you can ignore this email.'
        )
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)

        return Response(generic, status=status.HTTP_200_OK)


class LoginAPIView(APIView):
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ForgotPasswordAPIView(APIView):
    def post(self, request):
        serializer = ForgotPasswordRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        user = User.objects.filter(email__iexact=email).first()
        if user:
            otp = PasswordResetOTP.objects.create(user=user)
            subject = 'AoneGt Password Reset OTP'
            message = (
                f'Hello {user.first_name},\n\n'
                f'Your OTP for password reset is: {otp.otp_code}\n'
                f'This OTP will expire in 10 minutes.\n\n'
                f'Reset URL: {settings.FRONTEND_RESET_URL}\n\n'
                f'If you did not request this, please ignore this email.'
            )
            send_mail(
                subject, message, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False,
            )

        return Response(
            {
                'message': (
                    'If an account exists for this email, a password reset code has been sent.'
                ),
                'email': email,
            },
            status=status.HTTP_200_OK,
        )


class ResetPasswordAPIView(APIView):
    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email'].lower()
        otp_code = serializer.validated_data['otp']
        new_password = serializer.validated_data['new_password']

        user = User.objects.filter(email__iexact=email).first()
        otp = None
        if user:
            otp = PasswordResetOTP.objects.filter(
                user=user, otp_code=otp_code, is_used=False,
            ).first()
        if not user or not otp or otp.is_expired:
            return Response(
                {'detail': 'Invalid or expired reset request.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save(update_fields=['password'])
        otp.is_used = True
        otp.save(update_fields=['is_used'])

        return Response({'message': 'Password reset successful.'}, status=status.HTTP_200_OK)


class ProfileAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserProfileSerializer(request.user).data, status=status.HTTP_200_OK)

`

## accounts\__init__.py

`python

`

## accounts\services\zoho_commerce_contact.py

`python
"""
Check if an email appears on Zoho Commerce sales orders (search node `email`).

OAuth scope: ZohoCommerce.salesorders.READ
Header: X-com-zoho-store-organizationid (Commerce org id)

Docs: https://www.zoho.com/commerce/api/list-all-sales-orders.html

Limitation: this is true only if the email has at least one sales order in Commerce.
A customer in the Commerce UI with no orders yet may not match.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .zoho_inventory_contact import ZohoContactCheckError

COMMERCE_SALESORDERS_URL = 'https://commerce.zoho.com/store/api/v1/salesorders'


def zoho_commerce_check_configured() -> bool:
    token = (os.environ.get('ZOHO_ACCESS_TOKEN') or '').strip()
    org = (os.environ.get('ZOHO_COMMERCE_ORGANIZATION_ID') or '').strip()
    return bool(token and org)


def commerce_salesorders_email_exists(email: str) -> bool:
    email = (email or '').strip().lower()
    if not email:
        raise ZohoContactCheckError('Email is required.')

    token = (os.environ.get('ZOHO_ACCESS_TOKEN') or '').strip()
    org_id = (os.environ.get('ZOHO_COMMERCE_ORGANIZATION_ID') or '').strip()
    if not token or not org_id:
        raise ZohoContactCheckError(
            'Missing ZOHO_ACCESS_TOKEN or ZOHO_COMMERCE_ORGANIZATION_ID.',
        )

    qs = urlencode(
        {
            'email': email,
            'per_page': 50,
            'page': 1,
        },
    )
    url = f'{COMMERCE_SALESORDERS_URL}?{qs}'
    req = Request(
        url,
        headers={
            'Authorization': f'Zoho-oauthtoken {token}',
            'X-com-zoho-store-organizationid': org_id,
        },
        method='GET',
    )
    try:
        with urlopen(req, timeout=45) as resp:
            body = resp.read().decode('utf-8')
    except HTTPError as e:
        err = e.read().decode('utf-8', errors='replace')
        raise ZohoContactCheckError(f'Zoho Commerce HTTP {e.code}: {err}') from e
    except URLError as e:
        raise ZohoContactCheckError(f'Could not reach Zoho Commerce: {e}') from e

    try:
        data: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as e:
        raise ZohoContactCheckError('Invalid JSON from Zoho Commerce.') from e

    if data.get('code') != 0:
        msg = data.get('message') or data
        raise ZohoContactCheckError(f'Zoho Commerce error: {msg}')

    for so in data.get('salesorders') or []:
        em = (so.get('email') or '').strip().lower()
        if em == email:
            return True
    return False

`

## accounts\services\zoho_inventory_contact.py

`python
"""
Verify that an email exists as a Zoho Inventory contact (customer).

Uses List contacts with the `email` query parameter.
OAuth scope: ZohoInventory.contacts.READ

Docs: https://www.zoho.com/inventory/api/v1/contacts/#list-contacts
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ZohoContactCheckError(Exception):
    """Zoho API or configuration error while checking contact email."""


def zoho_contact_check_configured() -> bool:
    token = (os.environ.get('ZOHO_ACCESS_TOKEN') or '').strip()
    org = (os.environ.get('ZOHO_INVENTORY_ORGANIZATION_ID') or '').strip()
    return bool(token and org)


def _api_base() -> str:
    return (os.environ.get('ZOHO_API_BASE_HOST') or 'https://www.zohoapis.com').rstrip('/')


def inventory_contact_email_exists(email: str) -> bool:
    """
    Return True if a Zoho Inventory contact matches this email (exact, case-insensitive).

    Raises ZohoContactCheckError on HTTP/parse/configuration errors.
    """
    email = (email or '').strip().lower()
    if not email:
        raise ZohoContactCheckError('Email is required.')

    token = (os.environ.get('ZOHO_ACCESS_TOKEN') or '').strip()
    org_id = (os.environ.get('ZOHO_INVENTORY_ORGANIZATION_ID') or '').strip()
    if not token or not org_id:
        raise ZohoContactCheckError(
            'Missing ZOHO_ACCESS_TOKEN or ZOHO_INVENTORY_ORGANIZATION_ID.',
        )

    qs = urlencode(
        {
            'organization_id': org_id,
            'email': email,
            'per_page': 200,
        },
    )
    url = f'{_api_base()}/inventory/v1/contacts?{qs}'
    req = Request(
        url,
        headers={'Authorization': f'Zoho-oauthtoken {token}'},
        method='GET',
    )
    try:
        with urlopen(req, timeout=45) as resp:
            body = resp.read().decode('utf-8')
    except HTTPError as e:
        err = e.read().decode('utf-8', errors='replace')
        raise ZohoContactCheckError(f'Zoho Inventory HTTP {e.code}: {err}') from e
    except URLError as e:
        raise ZohoContactCheckError(f'Could not reach Zoho: {e}') from e

    try:
        data: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as e:
        raise ZohoContactCheckError('Invalid JSON from Zoho Inventory.') from e

    if data.get('code') != 0:
        msg = data.get('message') or data
        raise ZohoContactCheckError(f'Zoho Inventory error: {msg}')

    for c in data.get('contacts') or []:
        if _contact_matches_email(c, email):
            return True
        for p in c.get('contact_persons') or []:
            if (p.get('email') or '').strip().lower() == email:
                return True
    return False


def _contact_matches_email(contact: dict, email_lower: str) -> bool:
    main = (contact.get('email') or '').strip().lower()
    if main == email_lower:
        return True
    return False

`

## accounts\services\zoho_registration_gate.py

`python
"""
Single entry for registration-time Zoho email checks (Inventory vs Commerce).

Commerce mode looks up the email on Zoho Commerce **sales orders** (API filter `email`),
not a generic “customer exists” endpoint. Aliases: commerce, zoho_commerce, commerce_salesorders.
"""
from django.conf import settings

from .zoho_commerce_contact import commerce_salesorders_email_exists, zoho_commerce_check_configured
from .zoho_inventory_contact import (
    ZohoContactCheckError,
    inventory_contact_email_exists,
    zoho_contact_check_configured,
)


def resolved_register_zoho_email_source() -> str:
    """Return 'commerce_salesorders' or 'inventory' from REGISTER_ZOHO_EMAIL_SOURCE."""
    raw = (getattr(settings, 'REGISTER_ZOHO_EMAIL_SOURCE', 'inventory') or 'inventory').strip().lower()
    if raw in ('commerce_salesorders', 'commerce', 'zoho_commerce'):
        return 'commerce_salesorders'
    return 'inventory'


def registration_email_check_configured() -> bool:
    """True if env vars are set for the active REGISTER_ZOHO_EMAIL_SOURCE."""
    if resolved_register_zoho_email_source() == 'commerce_salesorders':
        return zoho_commerce_check_configured()
    return zoho_contact_check_configured()


def registration_email_exists_in_zoho(normalized_email: str) -> bool:
    if resolved_register_zoho_email_source() == 'commerce_salesorders':
        return commerce_salesorders_email_exists(normalized_email)
    return inventory_contact_email_exists(normalized_email)


__all__ = [
    'ZohoContactCheckError',
    'registration_email_check_configured',
    'registration_email_exists_in_zoho',
    'resolved_register_zoho_email_source',
]

`

## accounts\services\__init__.py

`python


`

## aonegt\asgi.py

`python
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aonegt.settings')
application = get_asgi_application()

`

## aonegt\settings.py

`python
from pathlib import Path
import os
from datetime import timedelta
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY') or 'change-me-set-DJANGO_SECRET_KEY-in-env'
DEBUG = os.getenv('DEBUG', 'True') == 'True'
ALLOWED_HOSTS = [
    h.strip() for h in os.getenv('ALLOWED_HOSTS', '127.0.0.1,localhost').split(',') if h.strip()
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'accounts',
    'catalog',
    'shop',
    'zoho_integration',
    'offers',
    
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'aonegt.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'aonegt.wsgi.application'
ASGI_APPLICATION = 'aonegt.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'aonegt_db'),
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Dubai'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'accounts.User'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.AllowAny',
    ),
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'AUTH_HEADER_TYPES': ('Bearer',),
}

if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True
else:
    CORS_ALLOW_ALL_ORIGINS = False
    _cors_origins = os.getenv('CORS_ALLOWED_ORIGINS', '').strip()
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors_origins.split(',') if o.strip()]

EMAIL_BACKEND = os.getenv(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend',
)
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True') == 'True'
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'webmaster@localhost')
FRONTEND_RESET_URL = os.getenv('FRONTEND_RESET_URL', 'aonegt://reset-password')

# --- Checkout: do not trust client shipping by default (set true only for dev / custom quotes). ---
CHECKOUT_TRUST_CLIENT_SHIPPING = os.getenv(
    'CHECKOUT_TRUST_CLIENT_SHIPPING', 'False',
).strip().lower() in ('true', '1', 'yes')
try:
    DEFAULT_SHIPPING_AMOUNT = Decimal(os.getenv('DEFAULT_SHIPPING_AMOUNT', '0'))
except Exception:
    DEFAULT_SHIPPING_AMOUNT = Decimal('0')

# --- Zoho: registration gate ---
REGISTER_REQUIRE_ZOHO_CONTACT = os.getenv(
    'REGISTER_REQUIRE_ZOHO_CONTACT', 'False',
).strip().lower() in ('true', '1', 'yes')
# inventory = Zoho Inventory contacts. commerce_salesorders | commerce | zoho_commerce = Zoho Commerce sales orders by email.
REGISTER_ZOHO_EMAIL_SOURCE = os.getenv(
    'REGISTER_ZOHO_EMAIL_SOURCE', 'inventory',
).strip().lower()
ZOHO_API_BASE_HOST = os.getenv('ZOHO_API_BASE_HOST', 'https://www.zohoapis.com').rstrip('/')
ZOHO_INVENTORY_ORGANIZATION_ID = os.getenv('ZOHO_INVENTORY_ORGANIZATION_ID', '').strip()
ZOHO_COMMERCE_ORGANIZATION_ID = os.getenv('ZOHO_COMMERCE_ORGANIZATION_ID', '').strip()

# When True, register requires POST /api/auth/request-registration-code/ first, then registration_otp on register.
REGISTER_REQUIRE_EMAIL_OTP = os.getenv(
    'REGISTER_REQUIRE_EMAIL_OTP', 'False',
).strip().lower() in ('true', '1', 'yes')

# --- Zoho Commerce: OAuth refresh + storefront (see shop.services.zoho_commerce.ZohoCommerceService) ---
ZOHO_COMMERCE_BASE_URL = os.getenv('ZOHO_COMMERCE_BASE_URL', 'https://commerce.zoho.com').rstrip('/')
ZOHO_ACCOUNTS_URL = os.getenv('ZOHO_ACCOUNTS_URL', 'https://accounts.zoho.com').rstrip('/')
ZOHO_STORE_DOMAIN = os.getenv('ZOHO_STORE_DOMAIN', '').strip()
ZOHO_ORG_ID = (os.getenv('ZOHO_ORG_ID') or ZOHO_COMMERCE_ORGANIZATION_ID or '').strip()
ZOHO_CLIENT_ID = os.getenv('ZOHO_CLIENT_ID', '').strip()
ZOHO_CLIENT_SECRET = os.getenv('ZOHO_CLIENT_SECRET', '').strip()
ZOHO_REFRESH_TOKEN = os.getenv('ZOHO_REFRESH_TOKEN', '').strip()


ZOHO_REDIRECT_URI = os.getenv("ZOHO_REDIRECT_URI")

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=5),   # 👈 increase this
    'REFRESH_TOKEN_LIFETIME': timedelta(days=5),
}


MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


`

## aonegt\urls.py

`python
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('accounts.urls')),
    path('api/catalog/', include('catalog.urls')),
    path('api/shop/', include('shop.urls')),
    path("zoho/", include("zoho_integration.urls")),
    path("api/offers/", include("offers.urls"), name='offers'),
     
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

`

## aonegt\wsgi.py

`python
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aonegt.settings')
application = get_wsgi_application()

`

## aonegt\__init__.py

`python

`

## catalog\admin.py

`python
from django.contrib import admin
from .models import Store, Product


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'contact_email',
        'category',
        'slug',
        'is_active',
        'sort_order',
        'zoho_org_id',
        'zoho_store_domain',
        'created_at',
    )
    list_filter = ('is_active',)
    search_fields = ('name', 'slug', 'contact_email', 'category', 'zoho_org_id')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('created_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'name',
                    'slug',
                    'contact_email',
                    'category',
                    'description',
                    'logo_url',
                    'is_active',
                    'sort_order',
                )
            },
        ),
        ('Zoho Commerce', {'fields': ('zoho_org_id', 'zoho_store_domain')}),
        (
            'Zoho OAuth (optional; per-store — falls back to global env)',
            {'fields': ('client_id', 'client_secret', 'refresh_token', 'access_token', 'token_expiry')},
        ),
        ('Meta', {'fields': ('created_at',)}),
    )


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'store', 'category', 'sku', 'price', 'currency', 'is_active')
    list_filter = ('is_active', 'store', 'currency')
    search_fields = ('name', 'slug', 'category', 'sku', 'zoho_product_id')
    prepopulated_fields = {'slug': ('name',)}
    autocomplete_fields = ('store',)

`

## catalog\apps.py

`python
from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'catalog'
    verbose_name = 'Catalog'

`

## catalog\models.py

`python
from django.db import models


class Store(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    contact_email = models.EmailField(blank=True)
    category = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    logo_url = models.URLField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    zoho_org_id = models.CharField(
        max_length=120,
        blank=True,
        help_text=(
            'Zoho Commerce organization id (header X-com-zoho-store-organizationid). '
            'Per-store; falls back to ZOHO_COMMERCE_ORGANIZATION_ID when empty.'
        ),
    )
    zoho_store_domain = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            'Storefront host for Zoho (e.g. mystore.zohostore.com), sent as domain-name. '
            'Per-store; falls back to ZOHO_STORE_DOMAIN when empty.'
        ),
    )
    client_id = models.CharField(max_length=255, blank=True)
    client_secret = models.CharField(max_length=255, blank=True)
    refresh_token = models.TextField(blank=True)
    access_token = models.TextField(blank=True)
    token_expiry = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class Product(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    category = models.CharField(max_length=255, blank=True)
    sku = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    compare_at_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
    )
    currency = models.CharField(max_length=8, default='AED')
    image_url = models.URLField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    zoho_product_id = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['store', 'slug'],
                name='catalog_product_store_slug_uniq',
            ),
        ]

    def __str__(self):
        return f'{self.name} ({self.store.name})'

`

## catalog\serializers.py

`python
from rest_framework import serializers
from .models import Store, Product


class StoreListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = (
            'id', 'name', 'slug', 'contact_email', 'category', 'description', 'logo_url', 'sort_order',
        )


class ProductListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = (
            'id', 'name', 'slug', 'category', 'sku', 'price', 'compare_at_price',
            'currency', 'image_url',
        )


class ProductDetailSerializer(serializers.ModelSerializer):
    store = StoreListSerializer(read_only=True)

    class Meta:
        model = Product
        fields = (
            'id', 'store', 'name', 'slug', 'category', 'sku', 'description',
            'price', 'compare_at_price', 'currency', 'image_url',
            'created_at', 'updated_at',
        )


class StoreAdminSerializer(serializers.ModelSerializer):
    """Staff-only: create/update stores (Django admin or Bearer token with is_staff)."""

    class Meta:
        model = Store
        fields = (
            'id', 'name', 'slug', 'contact_email', 'category', 'description', 'logo_url', 'is_active',
            'zoho_org_id', 'zoho_store_domain',
            'client_id', 'client_secret', 'refresh_token', 'access_token', 'token_expiry',
            'created_at', 'sort_order',
        )
        read_only_fields = ('id', 'created_at')


class ProductAdminSerializer(serializers.ModelSerializer):
    """Staff-only: create/update products under a store (store set from URL on create)."""

    class Meta:
        model = Product
        fields = (
            'id', 'store', 'name', 'slug', 'category', 'sku', 'description', 'price',
            'compare_at_price', 'currency', 'image_url', 'is_active',
            'zoho_product_id', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'store', 'created_at', 'updated_at')

`

## catalog\urls.py

`python
from django.urls import path
from .views import (
    StoreListAPIView,
    StoreProductListAPIView,
    StoreProductDetailAPIView,
    ZohoCommerceShopListAPIView,
    ZohoCommerceShopProductListAPIView,
    ZohoCommerceProductsProxyAPIView,
    ZohoCommerceProductDetailProxyAPIView,
    AdminStoreListCreateAPIView,
    AdminStoreDetailAPIView,
    AdminStoreProductListCreateAPIView,
    AdminStoreProductDetailAPIView,
)

urlpatterns = [
    path(
        'zoho/shops/',
        ZohoCommerceShopListAPIView.as_view(),
        name='catalog-zoho-shop-list',
    ),
    path(
        'zoho/shops/<str:shop_id>/products/',
        ZohoCommerceShopProductListAPIView.as_view(),
        name='catalog-zoho-shop-product-list',
    ),
    path(
        'zoho-commerce/products/',
        ZohoCommerceProductsProxyAPIView.as_view(),
        name='catalog-zoho-commerce-products-proxy',
    ),
    path(
        'zoho-commerce/products/<str:product_id>/',
        ZohoCommerceProductDetailProxyAPIView.as_view(),
        name='catalog-zoho-commerce-product-proxy',
    ),
    path('admin/stores/', AdminStoreListCreateAPIView.as_view(), name='catalog-admin-store-list-create'),
    path('admin/stores/<int:pk>/', AdminStoreDetailAPIView.as_view(), name='catalog-admin-store-detail'),
    path(
        'admin/stores/<int:store_id>/products/',
        AdminStoreProductListCreateAPIView.as_view(),
        name='catalog-admin-store-products',
    ),
    path(
        'admin/stores/<int:store_id>/products/<int:pk>/',
        AdminStoreProductDetailAPIView.as_view(),
        name='catalog-admin-store-product-detail',
    ),
    path('stores/', StoreListAPIView.as_view(), name='catalog-store-list'),
    path(
        'stores/<int:store_id>/products/',
        StoreProductListAPIView.as_view(),
        name='catalog-store-products',
    ),
    path(
        'stores/<int:store_id>/products/<int:pk>/',
        StoreProductDetailAPIView.as_view(),
        name='catalog-store-product-detail',
    ),
    
]

`

## catalog\views.py

`python
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Store, Product
from .services.zoho_commerce_products import (
    ZohoCommerceProductError,
    build_product_editpage_url,
    build_products_list_url,
    zoho_commerce_proxy_get,
)
from .services.zoho_sites import (
    fetch_zoho_shop_products,
    fetch_zoho_shops_from_accounts,
)
from shop.services.zoho_commerce import ZohoCommerceError
from .serializers import (
    StoreListSerializer,
    ProductListSerializer,
    ProductDetailSerializer,
    StoreAdminSerializer,
    ProductAdminSerializer,
)


def _optional_store_for_zoho_proxy(request):
    raw = request.query_params.get('store_id')
    if raw is None or str(raw).strip() == '':
        return None, None
    try:
        pk = int(raw)
    except (TypeError, ValueError):
        return None, Response(
            {'detail': 'store_id must be an integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    store = Store.objects.filter(pk=pk).first()
    if not store:
        return None, Response(
            {'detail': 'Store not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    return store, None


class ProductPageNumberPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class StoreListAPIView(generics.ListAPIView):
    """
    GET — list all active stores (your 9 storefronts).
    """
    serializer_class = StoreListSerializer
    queryset = Store.objects.filter(is_active=True)


class StoreProductListAPIView(generics.ListAPIView):
    """
    GET — paginated products for one store.
    Query: search (name/sku), page, page_size
    """
    serializer_class = ProductListSerializer
    pagination_class = ProductPageNumberPagination

    def get_queryset(self):
        store = get_object_or_404(Store, pk=self.kwargs['store_id'], is_active=True)
        qs = Product.objects.filter(store=store, is_active=True).order_by('name')
        q = (self.request.query_params.get('search') or '').strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q))
        return qs


class ZohoCommerceShopListAPIView(APIView):
    """
    GET — list shops from Zoho Commerce sites index in a mobile-friendly shape.

    Query:
    - account_id=<zoho account pk> (optional): fetch shops for one configured
      ZohoCommerceAccount; omitted means all active accounts.
    """

    def get(self, request):
        raw_account_id = (request.query_params.get('account_id') or '').strip()
        account_id = None
        if raw_account_id:
            try:
                account_id = int(raw_account_id)
            except (TypeError, ValueError):
                return Response(
                    {'status': 'error', 'message': 'account_id must be an integer.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        try:
            data = fetch_zoho_shops_from_accounts(account_id=account_id)
        except ZohoCommerceError as e:
            return Response(
                {'status': 'error', 'message': str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                'status': 'success',
                'message': 'Stores fetched successfully',
                'mode': 'accounts',
                'processed_account_count': data['processed_account_count'],
                'count': len(data['shops']),
                'stores': data['shops'],
                'errors': data['errors'],
            },
            status=status.HTTP_200_OK,
        )


class ZohoCommerceShopProductListAPIView(APIView):
    """
    GET — list products for a selected Zoho shop id.
    """

    def get(self, request, shop_id: str):
        account = (request.query_params.get('account') or 'primary').strip().lower()
        page = request.query_params.get('page', 1)
        per_page = request.query_params.get('per_page', 20)
        try:
            shop, products = fetch_zoho_shop_products(
                shop_id, page=page, per_page=per_page, account=account,
            )
        except ZohoCommerceError as e:
            msg = str(e)
            st = status.HTTP_404_NOT_FOUND if 'not found' in msg.lower() else status.HTTP_503_SERVICE_UNAVAILABLE
            return Response({'status': 'error', 'message': msg}, status=st)
        return Response(
            {
                'status': 'success',
                'message': 'Products fetched successfully',
                'account': account,
                'organization_id': shop.get('organization_id', ''),
                'shop': shop,
                'count': len(products),
                'products': products,
            },
            status=status.HTTP_200_OK,
        )


class ZohoCommerceProductsProxyAPIView(APIView):
    """
    GET — forwards query string to Zoho Commerce list products API; response body is Zoho JSON.

    Query (optional): ``store_id`` (local Store pk — uses ``zoho_org_id`` for org header),
    filter_by, sort_column, sort_order, page_start_from, per_page
    """

    def get(self, request):
        store, err = _optional_store_for_zoho_proxy(request)
        if err:
            return err
        url = build_products_list_url(dict(request.query_params))
        try:
            http_status, payload = zoho_commerce_proxy_get(url, store=store)
        except ZohoCommerceProductError as e:
            return Response({'detail': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if isinstance(payload, (dict, list)):
            return Response(payload, status=http_status)
        return Response({'detail': payload}, status=http_status)


class ZohoCommerceProductDetailProxyAPIView(APIView):
    """
    GET — Zoho Commerce product edit-page API (full product payload for one product_id).

    Query (optional): ``store_id`` — same as list proxy.
    """

    def get(self, request, product_id: str):
        store, err = _optional_store_for_zoho_proxy(request)
        if err:
            return err
        try:
            url = build_product_editpage_url(product_id)
            http_status, payload = zoho_commerce_proxy_get(url, store=store)
        except ZohoCommerceProductError as e:
            msg = str(e)
            st = (
                status.HTTP_400_BAD_REQUEST
                if 'required' in msg.lower()
                else status.HTTP_503_SERVICE_UNAVAILABLE
            )
            return Response({'detail': msg}, status=st)
        if isinstance(payload, (dict, list)):
            return Response(payload, status=http_status)
        return Response({'detail': payload}, status=http_status)


class StoreProductDetailAPIView(APIView):
    """
    GET — single product; store_id must match the product's store (safe for scoped IDs).
    """

    def get(self, request, store_id, pk):
        store = get_object_or_404(Store, pk=store_id, is_active=True)
        product = get_object_or_404(
            Product.objects.select_related('store'),
            pk=pk,
            store=store,
            is_active=True,
        )
        return Response(ProductDetailSerializer(product).data, status=status.HTTP_200_OK)


class AdminStoreListCreateAPIView(generics.ListCreateAPIView):
    """
    Staff only (JWT + is_staff). GET all stores; POST create a store.
    """
    permission_classes = [IsAdminUser]
    queryset = Store.objects.all().order_by('sort_order', 'name')
    serializer_class = StoreAdminSerializer


class AdminStoreDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    """Staff only. GET/PATCH/DELETE one store by id."""
    permission_classes = [IsAdminUser]
    queryset = Store.objects.all()
    serializer_class = StoreAdminSerializer


class AdminStoreProductListCreateAPIView(generics.ListCreateAPIView):
    """
    Staff only. GET all products for a store (including inactive); POST add product mapped to this store.
    """
    permission_classes = [IsAdminUser]
    serializer_class = ProductAdminSerializer

    def get_queryset(self):
        store = get_object_or_404(Store, pk=self.kwargs['store_id'])
        return Product.objects.filter(store=store).select_related('store').order_by('name')

    def perform_create(self, serializer):
        store = get_object_or_404(Store, pk=self.kwargs['store_id'])
        serializer.save(store=store)


class AdminStoreProductDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    """Staff only. GET/PATCH/DELETE product; must belong to store_id in URL."""
    permission_classes = [IsAdminUser]
    serializer_class = ProductAdminSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        return Product.objects.filter(store_id=self.kwargs['store_id']).select_related('store')


`

## catalog\__init__.py

`python

`

## catalog\management\__init__.py

`python


`

## catalog\management\commands\sync_zoho_products.py

`python
from django.core.management.base import BaseCommand, CommandError

from catalog.models import Store
from catalog.services.zoho_product_sync import (
    ZohoProductSyncError,
    iter_syncable_stores,
    sync_store_from_zoho,
)


class Command(BaseCommand):
    help = (
        'Fetch products from Zoho Commerce (GET /store/api/v1/products) and upsert local '
        'catalog rows by Zoho product/variant id. Requires ZohoCommerce.items.READ and '
        'ZOHO_ACCESS_TOKEN + ZOHO_COMMERCE_ORGANIZATION_ID (or per-store tokens/org).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--store-id',
            type=int,
            default=None,
            help='Sync only this local Store primary key.',
        )
        parser.add_argument(
            '--all-stores',
            action='store_true',
            help='Sync every active Store (each uses its own zoho_org_id when set).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse Zoho pages and count rows without writing the database.',
        )
        parser.add_argument(
            '--filter-by',
            type=str,
            default='Status.Active',
            help='Zoho filter_by (e.g. Status.Active, Status.All).',
        )
        parser.add_argument(
            '--per-page',
            type=int,
            default=100,
            help='Page size (10, 25, 50, 100, or 200 per Zoho).',
        )

    def handle(self, *args, **options):
        store_id = options['store_id']
        all_stores = options['all_stores']
        if store_id is None and not all_stores:
            raise CommandError('Pass --store-id <id> or --all-stores.')
        if store_id is not None and all_stores:
            raise CommandError('Use either --store-id or --all-stores, not both.')

        qs = iter_syncable_stores()
        if store_id is not None:
            store = Store.objects.filter(pk=store_id).first()
            if not store:
                raise CommandError(f'Store id={store_id} not found.')
            stores = [store]
        else:
            stores = list(qs)

        for store in stores:
            self.stdout.write(f'Syncing store pk={store.pk} ({store.name!r}) …')
            try:
                stats = sync_store_from_zoho(
                    store,
                    filter_by=options['filter_by'],
                    per_page=options['per_page'],
                    dry_run=options['dry_run'],
                )
            except (ZohoProductSyncError, OSError) as e:
                raise CommandError(str(e)) from e

            self.stdout.write(self.style.SUCCESS(f"  pages={stats['pages']} raw_products={stats['raw_products']} rows={stats['rows']}"))
            if options['dry_run']:
                self.stdout.write(self.style.WARNING('  dry-run: no database writes'))
            else:
                self.stdout.write(
                    f"  created={stats['created']} updated={stats['updated']} unchanged={stats['unchanged']}",
                )
            for err in stats.get('errors') or []:
                self.stdout.write(self.style.ERROR(f'  error: {err}'))

`

## catalog\management\commands\__init__.py

`python


`

## catalog\services\zoho_commerce_products.py

`python
"""
Proxy helpers for Zoho Commerce store APIs (same auth as sales orders).

List: GET https://commerce.zoho.com/store/api/v1/products
Detail (edit page): GET .../products/editpage?product_id=

OAuth scope: ZohoCommerce.items.READ
Headers: Authorization Zoho-oauthtoken, X-com-zoho-store-organizationid
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

COMMERCE_PRODUCTS_LIST_URL = 'https://commerce.zoho.com/store/api/v1/products'
COMMERCE_PRODUCT_EDITPAGE_URL = 'https://commerce.zoho.com/store/api/v1/products/editpage'

LIST_QUERY_KEYS = frozenset(
    {'filter_by', 'sort_column', 'sort_order', 'page_start_from', 'per_page'},
)


class ZohoCommerceProductError(Exception):
    """Configuration or transport error before a parsed HTTP response is returned."""


def _resolved_commerce_org_id(store: object | None) -> str:
    if store is not None:
        org = (getattr(store, 'zoho_org_id', '') or '').strip()
        if org:
            return org
    return (os.environ.get('ZOHO_COMMERCE_ORGANIZATION_ID') or '').strip()


def _bearer_token_for_store(store: object | None) -> str:
    if store is not None:
        from django.utils import timezone as dj_tz

        at = (getattr(store, 'access_token', '') or '').strip()
        exp = getattr(store, 'token_expiry', None)
        if at and (exp is None or exp > dj_tz.now()):
            return at
    return (os.environ.get('ZOHO_ACCESS_TOKEN') or '').strip()


def _store_auth_headers(store: object | None = None) -> dict[str, str]:
    token = _bearer_token_for_store(store)
    org_id = _resolved_commerce_org_id(store)
    if not token or not org_id:
        raise ZohoCommerceProductError(
            'Set ZOHO_ACCESS_TOKEN (or Store.access_token) and a Commerce org id '
            'on the Store (zoho_org_id) or ZOHO_COMMERCE_ORGANIZATION_ID in the environment.',
        )
    return {
        'Authorization': f'Zoho-oauthtoken {token}',
        'X-com-zoho-store-organizationid': org_id,
    }


def zoho_commerce_proxy_get(url: str, *, store: object | None = None) -> tuple[int, Any]:
    """
    GET url with Commerce store headers. Returns (http_status, body).
    body is parsed JSON when possible; otherwise a string of the response body.

    Pass ``store`` to use that store's ``zoho_org_id`` as organization header
    and optional ``access_token`` when valid.
    """
    req = Request(url, headers=_store_auth_headers(store), method='GET')
    try:
        with urlopen(req, timeout=60) as resp:
            raw = resp.read().decode('utf-8')
            status = getattr(resp, 'status', 200) or 200
    except HTTPError as e:
        status = e.code
        raw = e.read().decode('utf-8', errors='replace')
    except URLError as e:
        raise ZohoCommerceProductError(f'Could not reach Zoho Commerce: {e}') from e

    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def build_products_list_url(query_params: dict[str, Any]) -> str:
    items = []
    for key in LIST_QUERY_KEYS:
        if key not in query_params:
            continue
        val = query_params.get(key)
        if val is None or val == '':
            continue
        items.append((key, val))
    if not items:
        return COMMERCE_PRODUCTS_LIST_URL
    return f'{COMMERCE_PRODUCTS_LIST_URL}?{urlencode(items, doseq=True)}'


def build_product_editpage_url(product_id: str) -> str:
    product_id = (product_id or '').strip()
    if not product_id:
        raise ZohoCommerceProductError('product_id is required.')
    return f'{COMMERCE_PRODUCT_EDITPAGE_URL}?{urlencode({"product_id": product_id})}'

`

## catalog\services\zoho_product_sync.py

`python
"""
Pull products from Zoho Commerce store API (GET /store/api/v1/products) and upsert local
:class:`~catalog.models.Product` rows keyed by stable Zoho ids (``variant_id`` when variants
exist, else ``product_id``).

OAuth: ``ZohoCommerce.items.READ`` — same headers as :mod:`catalog.services.zoho_commerce_products`.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import transaction
from django.utils.text import slugify

from catalog.models import Product, Store
from catalog.services.zoho_commerce_products import (
    ZohoCommerceProductError,
    build_products_list_url,
    zoho_commerce_proxy_get,
)


class ZohoProductSyncError(Exception):
    """Unsuccessful Zoho response or unexpected payload during catalog sync."""


def _safe_decimal(val: Any) -> Decimal | None:
    if val is None or val == '':
        return None
    try:
        return Decimal(str(val)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _description_from_zoho_product(raw: dict[str, Any]) -> str:
    parts = [
        raw.get('product_description') or '',
        raw.get('description') or '',
        raw.get('product_short_description') or '',
    ]
    text = '\n\n'.join((p.strip() for p in parts if p and str(p).strip()))
    return (text or '')[:5000]


def _variant_option_suffix(variant: dict[str, Any]) -> str:
    parts: list[str] = []
    for i in (1, 2, 3):
        data = (variant.get(f'attribute_option_data{i}') or '').strip()
        name = (variant.get(f'attribute_option_name{i}') or '').strip()
        if data:
            parts.append(data)
        elif name:
            parts.append(name)
    return ', '.join(parts)


def _variant_display_name(base_name: str, variant: dict[str, Any]) -> str:
    suffix = _variant_option_suffix(variant)
    if suffix:
        return f'{base_name} ({suffix})'
    vn = (variant.get('name') or '').strip()
    if vn and vn != base_name:
        return vn
    return base_name


def _row_active(raw: dict[str, Any], variant: dict[str, Any] | None) -> bool:
    if raw.get('show_in_storefront') is False:
        return False
    if (raw.get('status') or '').lower() != 'active':
        return False
    if variant is not None and (variant.get('status') or 'active').lower() != 'active':
        return False
    return True


def expand_zoho_list_product(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Normalize one Zoho list payload product dict into sellable rows (one per variant, or one
    synthetic row if the API omits variants).
    """
    base_name = (raw.get('name') or '').strip() or 'Product'
    product_id = str(raw.get('product_id') or '').strip()
    url_hint = (raw.get('url') or '').strip()
    category = (raw.get('category_name') or raw.get('category') or '').strip()
    desc = _description_from_zoho_product(raw)
    variants = raw.get('variants')

    rows: list[dict[str, Any]] = []
    if not variants:
        if not product_id:
            return rows
        rate = raw.get('min_rate') or raw.get('rate') or 0
        price = _safe_decimal(rate) or Decimal('0')
        compare = _safe_decimal(raw.get('max_rate'))
        if compare is not None and compare <= 0:
            compare = None
        rows.append({
            'zoho_product_id': product_id,
            'name': base_name,
            'slug_hint': url_hint or base_name,
            'sku': (raw.get('sku') or '').strip(),
            'price': price,
            'compare_at_price': compare,
            'description': desc,
            'category': category[:255] if category else '',
            'is_active': _row_active(raw, None),
        })
        return rows

    for v in variants:
        if not isinstance(v, dict):
            continue
        vid = str(v.get('variant_id') or '').strip()
        if not vid:
            continue
        price = _safe_decimal(v.get('rate')) or Decimal('0')
        compare = _safe_decimal(v.get('label_rate'))
        if compare is not None and compare > 0 and compare <= price:
            compare = None
        rows.append({
            'zoho_product_id': vid,
            'name': _variant_display_name(base_name, v),
            'slug_hint': url_hint or base_name,
            'sku': (v.get('sku') or '').strip()[:120],
            'price': price,
            'compare_at_price': compare,
            'description': desc,
            'category': category[:255] if category else '',
            'is_active': _row_active(raw, v),
        })
    if not rows and product_id:
        rate = raw.get('min_rate') or raw.get('rate') or 0
        price = _safe_decimal(rate) or Decimal('0')
        compare = _safe_decimal(raw.get('max_rate'))
        if compare is not None and compare <= 0:
            compare = None
        if compare is not None and compare <= price:
            compare = None
        rows.append({
            'zoho_product_id': product_id,
            'name': base_name,
            'slug_hint': url_hint or base_name,
            'sku': (raw.get('sku') or '').strip()[:120],
            'price': price,
            'compare_at_price': compare,
            'description': desc,
            'category': category[:255] if category else '',
            'is_active': _row_active(raw, None),
        })
    return rows


def _resolve_unique_slug(store: Store, base: str, zoho_id: str, product: Product | None) -> str:
    root = slugify(base)[:180] or 'item'
    zpart = slugify(zoho_id.replace('/', '-'))[:80] or 'id'
    for candidate in (f'{root}-{zpart}', f'{root}-{zpart}-store{store.pk}'):
        slug = candidate[:255]
        qs = Product.objects.filter(store=store, slug=slug)
        if product is not None:
            qs = qs.exclude(pk=product.pk)
        if not qs.exists():
            return slug
    return f'{root}-{zpart}-s{store.pk}-z{zoho_id}'[:255]


def _upsert_product(store: Store, row: dict[str, Any]) -> tuple[str, Product]:
    zid = row['zoho_product_id']
    product = Product.objects.filter(store=store, zoho_product_id=zid).first()
    slug = _resolve_unique_slug(store, row['slug_hint'], zid, product)
    defaults = {
        'name': row['name'][:255],
        'slug': slug,
        'sku': row['sku'],
        'description': row['description'],
        'price': row['price'],
        'compare_at_price': row['compare_at_price'],
        'category': row['category'],
        'is_active': row['is_active'],
    }
    if product is None:
        product = Product(store=store, zoho_product_id=zid)
        for k, v in defaults.items():
            setattr(product, k, v)
        product.save()
        return 'created', product
    changed = False
    for k, v in defaults.items():
        if getattr(product, k) != v:
            setattr(product, k, v)
            changed = True
    if changed:
        product.save()
        return 'updated', product
    return 'unchanged', product


def _parse_list_response(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ZohoProductSyncError(f'Unexpected response type: {type(payload).__name__}')
    code = payload.get('code')
    try:
        ok = int(code) == 0
    except (TypeError, ValueError):
        ok = code in (0, '0', None)
    if not ok:
        msg = payload.get('message') or payload.get('error') or str(payload)
        raise ZohoProductSyncError(f'Zoho products API error: {msg}')
    products = payload.get('products')
    if products is None:
        raise ZohoProductSyncError('Zoho response missing "products" array.')
    if not isinstance(products, list):
        raise ZohoProductSyncError('Zoho "/products" is not a list.')
    page_ctx = payload.get('page_context') if isinstance(payload.get('page_context'), dict) else {}
    return products, page_ctx


def sync_store_from_zoho(
    store: Store,
    *,
    filter_by: str = 'Status.Active',
    per_page: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Fetch all pages from Zoho for one local :class:`~catalog.models.Store` and upsert products.

    :param store: Must be able to authenticate via store fields or ``ZOHO_*`` env (see proxy).
    :param filter_by: Zoho ``filter_by`` query (e.g. ``Status.Active``, ``Status.All``).
    :param per_page: Page size (Zoho allows 10, 25, 50, 100, 200).
    :param dry_run: If True, parse and count rows but do not write the database.
    :returns: Stats dict with keys ``pages``, ``raw_products``, ``rows``, ``created``, ``updated``,
              ``unchanged``, ``dry_run``, and optional ``errors`` (list of str).
    """
    allowed = (10, 25, 50, 100, 200)
    per_page = int(per_page)
    if per_page not in allowed:
        for v in allowed:
            if v >= per_page:
                per_page = v
                break
        else:
            per_page = 200

    stats: dict[str, Any] = {
        'pages': 0,
        'raw_products': 0,
        'rows': 0,
        'created': 0,
        'updated': 0,
        'unchanged': 0,
        'dry_run': dry_run,
        'errors': [],
    }
    page = 1
    while True:
        url = build_products_list_url({
            'filter_by': filter_by,
            'page_start_from': page,
            'per_page': per_page,
            'sort_column': 'name',
            'sort_order': 'A',
        })
        try:
            status, payload = zoho_commerce_proxy_get(url, store=store)
        except ZohoCommerceProductError as e:
            raise ZohoProductSyncError(str(e)) from e

        if status != 200:
            raise ZohoProductSyncError(
                f'Zoho HTTP {status}: {payload if isinstance(payload, str) else payload!r}',
            )

        products, page_ctx = _parse_list_response(payload)
        stats['pages'] += 1
        stats['raw_products'] += len(products)

        batch_rows: list[dict[str, Any]] = []
        for raw in products:
            if not isinstance(raw, dict):
                stats['errors'].append('Skipped non-object product row')
                continue
            try:
                batch_rows.extend(expand_zoho_list_product(raw))
            except Exception as e:
                stats['errors'].append(f'expand error {raw.get("product_id")}: {e}')

        stats['rows'] += len(batch_rows)

        if not dry_run and batch_rows:
            with transaction.atomic():
                for row in batch_rows:
                    try:
                        action, _p = _upsert_product(store, row)
                        stats[action] += 1
                    except Exception as e:
                        stats['errors'].append(f'upsert {row.get("zoho_product_id")}: {e}')

        has_more = bool(page_ctx.get('has_more_page'))
        if not has_more:
            break
        page += 1
        if page > 10000:
            stats['errors'].append('Stopped after 10000 pages (safety).')
            break

    if dry_run:
        stats['created'] = stats['updated'] = stats['unchanged'] = 0

    return stats


def iter_syncable_stores(queryset=None):
    """Active stores, optionally those with per-store org id (all still get env fallback in proxy)."""
    qs = queryset or Store.objects.filter(is_active=True).order_by('pk')
    return qs

`

## catalog\services\zoho_sites.py

`python
"""
Helpers for Zoho Commerce sites (shops) index API.

Docs endpoint:
    GET {ZOHO_COMMERCE_BASE_URL}/zs-site/api/v1/index/sites
"""
from __future__ import annotations

from typing import Any, Optional

import requests
from django.conf import settings

from catalog.models import Store
from zoho_integration.models import ZohoCommerceAccount
from shop.services.zoho_commerce import ZohoCommerceError, ZohoCommerceService


def _resolve_account_key(account: Optional[str]) -> str:
    key = str(account or 'primary').strip().lower()
    if key not in ('primary', 'secondary'):
        raise ZohoCommerceError("account must be 'primary' or 'secondary'.")
    return key


def _commerce_base_for_account(account: Optional[str]) -> str:
    key = _resolve_account_key(account)
    if key == 'secondary':
        return (
            getattr(settings, 'ZOHO_SECONDARY_COMMERCE_BASE_URL', '')
            or getattr(settings, 'ZOHO_COMMERCE_BASE_URL', '')
            or 'https://commerce.zoho.com'
        ).rstrip('/')
    return (getattr(settings, 'ZOHO_COMMERCE_BASE_URL', '') or 'https://commerce.zoho.com').rstrip('/')


def _refresh_access_token_for_account(account: Optional[str]) -> str:
    key = _resolve_account_key(account)
    if key == 'primary':
        return ZohoCommerceService.refresh_access_token()

    refresh_token = (getattr(settings, 'ZOHO_SECONDARY_REFRESH_TOKEN', '') or '').strip()
    client_id = (getattr(settings, 'ZOHO_SECONDARY_CLIENT_ID', '') or '').strip()
    client_secret = (getattr(settings, 'ZOHO_SECONDARY_CLIENT_SECRET', '') or '').strip()
    if not (refresh_token and client_id and client_secret):
        raise ZohoCommerceError(
            'Set ZOHO_SECONDARY_REFRESH_TOKEN, ZOHO_SECONDARY_CLIENT_ID, and '
            'ZOHO_SECONDARY_CLIENT_SECRET for secondary account shop listing.',
        )
    access_token, _exp = ZohoCommerceService._refresh_with_creds(
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
    )
    return access_token


def _extract_sites(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    get_sites = payload.get('get_sites')
    if not isinstance(get_sites, dict):
        return []
    my_sites = get_sites.get('my_sites')
    if not isinstance(my_sites, list):
        return []
    return [s for s in my_sites if isinstance(s, dict)]


def _map_shop(site: dict[str, Any]) -> dict[str, Any]:
    return {
        'shop_id': str(site.get('zsite_id') or '').strip(),
        'shop_name': str(site.get('site_title') or '').strip(),
        'domain': str(site.get('primary_domain') or '').strip(),
        'finance_org_id': str(site.get('zohofinance_orgid') or '').strip(),
        'organization_id': str(site.get('zohofinance_orgid') or '').strip(),
        'currency_code': str(site.get('currency_code') or '').strip(),
        'country_code': str(site.get('country_code') or '').strip(),
        'store_enabled': bool(site.get('store_enabled')),
    }


def _fetch_sites_with_token(base: str, token: str) -> Any:
    url = f'{base}/zs-site/api/v1/index/sites'
    headers = {'Authorization': f'Zoho-oauthtoken {token}'}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        status_code = getattr(resp, 'status_code', 'unknown')
        body = (getattr(resp, 'text', '') or '').strip()
        details = body[:500] if body else 'no response body'
        raise ZohoCommerceError(
            f'Zoho sites request failed (HTTP {status_code}): {details}',
        ) from e
    except requests.RequestException as e:
        raise ZohoCommerceError(f'Zoho sites request failed: {e}') from e
    except ValueError as e:
        raise ZohoCommerceError('Invalid JSON from Zoho sites endpoint.') from e


def _refresh_access_token_for_account_model(account: ZohoCommerceAccount) -> str:
    token_url = f"{(account.accounts_url or 'https://accounts.zoho.com').rstrip('/')}/oauth/v2/token"
    payload = {
        'refresh_token': (account.refresh_token or '').strip(),
        'client_id': (account.client_id or '').strip(),
        'client_secret': (account.client_secret or '').strip(),
        'grant_type': 'refresh_token',
    }
    if not (payload['refresh_token'] and payload['client_id'] and payload['client_secret']):
        raise ZohoCommerceError(
            f'Account "{account.name}" is missing refresh_token/client_id/client_secret.',
        )
    try:
        response = requests.post(token_url, data=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        raise ZohoCommerceError(f'Zoho token refresh failed for "{account.name}": {e}') from e
    except ValueError as e:
        raise ZohoCommerceError(f'Invalid JSON from token endpoint for "{account.name}".') from e
    access_token = (data.get('access_token') or '').strip()
    if not access_token:
        raise ZohoCommerceError(f'No access_token returned for "{account.name}".')
    return access_token


def fetch_zoho_shops(*, account: str = 'primary') -> list[dict[str, Any]]:
    """
    Return normalized shop records for mobile UI.
    """
    base = _commerce_base_for_account(account)
    token = _refresh_access_token_for_account(account)
    payload = _fetch_sites_with_token(base, token)
    shops = [_map_shop(site) for site in _extract_sites(payload)]
    return [s for s in shops if s['shop_id']]


def fetch_zoho_shops_from_stores(*, store_id: int | None = None) -> dict[str, Any]:
    """
    Fetch Zoho shops using per-store OAuth credentials saved in catalog.Store.
    Returns summary with shops and per-store errors.
    """
    qs = Store.objects.filter(is_active=True).order_by('sort_order', 'name')
    if store_id is not None:
        qs = qs.filter(pk=store_id)
    stores = list(qs)
    if not stores:
        raise ZohoCommerceError('No active stores found for database-credentials mode.')

    base = (getattr(settings, 'ZOHO_COMMERCE_BASE_URL', '') or 'https://commerce.zoho.com').rstrip('/')
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for store in stores:
        try:
            token = ZohoCommerceService.refresh_access_token(store=store)
            payload = _fetch_sites_with_token(base, token)
            shops = [_map_shop(site) for site in _extract_sites(payload)]
            shops = [s for s in shops if s['shop_id']]
            for shop in shops:
                shop['store_pk'] = store.pk
                shop['store_name'] = store.name
            results.extend(shops)
        except ZohoCommerceError as e:
            errors.append(
                {
                    'store_pk': store.pk,
                    'store_name': store.name,
                    'error': str(e),
                }
            )

    return {
        'shops': results,
        'errors': errors,
        'processed_store_count': len(stores),
    }


def fetch_zoho_shops_from_accounts(*, account_id: int | None = None) -> dict[str, Any]:
    """
    Fetch Zoho shops using zoho_integration.ZohoCommerceAccount records.
    Returns summary with shops and per-account errors.
    """
    qs = ZohoCommerceAccount.objects.filter(is_active=True).order_by('name')
    if account_id is not None:
        qs = qs.filter(pk=account_id)
    accounts = list(qs)
    if not accounts:
        raise ZohoCommerceError('No active ZohoCommerceAccount records found.')

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for account in accounts:
        try:
            token = _refresh_access_token_for_account_model(account)
            base = (account.commerce_base_url or 'https://commerce.zoho.com').rstrip('/')
            payload = _fetch_sites_with_token(base, token)
            shops = [_map_shop(site) for site in _extract_sites(payload)]
            shops = [s for s in shops if s['shop_id']]
            for shop in shops:
                shop['account_id'] = account.pk
                shop['account_name'] = account.name
                shop['account_email'] = account.email
            results.extend(shops)
        except ZohoCommerceError as e:
            errors.append(
                {
                    'account_id': account.pk,
                    'account_name': account.name,
                    'account_email': account.email,
                    'error': str(e),
                }
            )

    return {
        'shops': results,
        'errors': errors,
        'processed_account_count': len(accounts),
    }


def _as_amount(raw: Any) -> str:
    if raw is None:
        return '0'
    text = str(raw).strip()
    return text or '0'


def _extract_products(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ('products', 'data', 'items'):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [p for p in rows if isinstance(p, dict)]
    return []


def _map_product(product: dict[str, Any]) -> dict[str, Any]:
    product_id = str(
        product.get('product_id')
        or product.get('id')
        or product.get('variant_id')
        or '',
    ).strip()
    return {
        'product_id': product_id,
        'name': str(product.get('name') or product.get('product_name') or '').strip(),
        'sku': str(product.get('sku') or '').strip(),
        'price': _as_amount(product.get('rate') or product.get('price') or product.get('min_rate')),
        'sale_price': _as_amount(product.get('sale_price')),
        'stock': _as_amount(product.get('stock_on_hand') or product.get('stock')),
        'image_url': str(product.get('image_url') or product.get('image_name') or '').strip(),
        'image_name': str(product.get('image_name') or '').strip(),
        'image_document_id': str(product.get('image_document_id') or '').strip(),
        'status': str(product.get('status') or '').strip(),
    }


def fetch_zoho_shop_products(
    shop_id: str,
    *,
    page: int = 1,
    per_page: int = 20,
    account: str = 'primary',
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Resolve a shop by id from Zoho sites and fetch storefront products for that shop domain.
    """
    sid = str(shop_id or '').strip()
    if not sid:
        raise ZohoCommerceError('shop_id is required.')

    shops = fetch_zoho_shops(account=account)
    shop = next((s for s in shops if s.get('shop_id') == sid), None)
    if not shop:
        raise ZohoCommerceError('Shop not found in Zoho sites.')
    domain = (shop.get('domain') or '').strip()
    if not domain:
        raise ZohoCommerceError('Selected shop has no primary domain.')

    base = _commerce_base_for_account(account)
    url = f'{base}/storefront/api/v1/products'
    params = {'page': int(page or 1), 'per_page': int(per_page or 20), 'format': 'json'}
    try:
        response = requests.get(url, headers={'domain-name': domain}, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as e:
        raise ZohoCommerceError(f'Zoho storefront products request failed: {e}') from e
    except ValueError as e:
        raise ZohoCommerceError('Invalid JSON from Zoho storefront products.') from e

    products = [_map_product(p) for p in _extract_products(payload)]
    products = [p for p in products if p['product_id']]
    return shop, products

`

## catalog\services\__init__.py

`python
# Catalog integration services (e.g. Zoho Commerce proxy).

`

## offers\admin.py

`python
from django.contrib import admin
from .models import Organization, WebhookConfig


class WebhookConfigInline(admin.TabularInline):
    model = WebhookConfig
    extra = 1
    fields = ['webhook_type', 'webhook_url', 'is_active']


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ['name', 'org_id', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'org_id']
    inlines = [WebhookConfigInline]


@admin.register(WebhookConfig)
class WebhookConfigAdmin(admin.ModelAdmin):
    list_display = ['organization', 'webhook_type', 'is_active']
    list_filter = ['webhook_type', 'is_active']
`

## offers\apps.py

`python
from django.apps import AppConfig


class OffersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'offers'

`

## offers\models.py

`python
from django.db import models


class Organization(models.Model):
    """
    Represents a Zoho Commerce store.
    org_id is the Zoho organization ID (e.g. "60070045641").
    """
    name = models.CharField(max_length=255)
    image = models.ImageField(upload_to='org_images/', null=True, blank=True)
    org_id = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.org_id})"


class WebhookConfig(models.Model):
    """
    Stores the full Zoho ZAPI key webhook URL per organization per action.

    The webhook_url is the complete URL including encapiKey query param,
    copied directly from Zoho Commerce → Settings → Developer Data →
    Incoming Webhooks → [webhook name] → ZAPI Key URL.

    Example:
    https://www.zohoapis.in/commerce/v1/settings/incomingwebhooks/
    iw_create_coupon_webhook/execute?auth_type=apikey&encapiKey=PHtE6r...

    Django POSTs JSON to this URL. No OAuth tokens are stored here.
    OAuth is handled entirely inside Zoho's Connection system.
    """
    WEBHOOK_TYPE_CHOICES = [
        ('create_coupon', 'Create Coupon'),
        ('list_coupons', 'List Coupons'),
        ('delete_coupon', 'Delete Coupon'),
        ('get_coupon', 'Get Coupon'),
        ('update_coupon', 'Update Coupon'),
    ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='webhooks'
    )
    webhook_type = models.CharField(max_length=50, choices=WEBHOOK_TYPE_CHOICES)
    webhook_url = models.TextField(
        help_text="Full ZAPI key URL from Zoho incoming webhook settings. Includes encapiKey."
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('organization', 'webhook_type')

    def __str__(self):
        return f"{self.organization.name} — {self.get_webhook_type_display()}"
`

## offers\serializers.py

`python
from rest_framework import serializers


# ── Existing (do not remove) ──────────────────────────────────────────────────

class SuperuserLoginSerializer(serializers.Serializer):
    """
    Handles basic input validation for superuser login credentials.
    Actual authentication is deferred to the service layer.
    """
    email = serializers.EmailField(required=True)
    password = serializers.CharField(
        write_only=True, required=True, style={'input_type': 'password'}
    )


# ── Organization ──────────────────────────────────────────────────────────────

class WebhookConfigSerializer(serializers.Serializer):
    webhook_type = serializers.CharField()
    is_active = serializers.BooleanField()


class OrganizationSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField()
    org_id = serializers.CharField()
    is_active = serializers.BooleanField()
    image_url = serializers.SerializerMethodField()
    webhooks = WebhookConfigSerializer(many=True, read_only=True)

    def get_image_url(self, obj):
        request = self.context.get('request')
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return None


# ── Coupons ───────────────────────────────────────────────────────────────────

class CouponCreateSerializer(serializers.Serializer):
    # Basic
    apply_as = serializers.ChoiceField(choices=['coupon', 'automatic_discount'])
    coupon_code = serializers.CharField(required=False, allow_blank=True, default='')
    coupon_name = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True, default='')
    is_active = serializers.BooleanField(default=True)
    show_in_storefront = serializers.BooleanField(default=False)

    # Discount type
    discount_type = serializers.ChoiceField(choices=[
        'order_flat',
        'order_percentage',
        'item_flat',
        'item_percentage',
        'free_shipping',
        'buy_x_get_y',
    ])
    discount_value = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False
    )
    max_discount_amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False
    )

    # item_flat / item_percentage: [{"product_id": "xxx"}, ...]
    eligible_product_ids = serializers.ListField(
        child=serializers.DictField(), required=False, default=list
    )

    # buy_x_get_y
    buying_quantity = serializers.IntegerField(required=False)
    buy_product_ids = serializers.ListField(
        child=serializers.DictField(), required=False, default=list
    )
    get_product_ids = serializers.ListField(
        child=serializers.DictField(), required=False, default=list
    )

    # Cart minimum
    minimum_cart_amount_enabled = serializers.BooleanField(default=False)
    minimum_cart_amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False
    )

    # Validity
    valid_from = serializers.CharField()
    never_expires = serializers.BooleanField(default=False)
    valid_till = serializers.CharField(required=False, allow_blank=True, default='')

    # Limits
    store_limit_enabled = serializers.BooleanField(default=False)
    store_limit = serializers.IntegerField(required=False)
    customer_limit_enabled = serializers.BooleanField(default=False)
    customer_limit = serializers.IntegerField(required=False)

    def validate(self, data):
        if data.get('apply_as') == 'coupon' and not data.get('coupon_code'):
            raise serializers.ValidationError(
                {"coupon_code": "Required when apply_as is 'coupon'."}
            )
        if not data.get('never_expires') and not data.get('valid_till'):
            raise serializers.ValidationError(
                {"valid_till": "Required when never_expires is false."}
            )
        if data.get('discount_type') not in ['free_shipping']:
            if data.get('discount_value') is None:
                raise serializers.ValidationError(
                    {"discount_value": "Required for this discount type."}
                )
        return data


class CouponDeleteSerializer(serializers.Serializer):
    coupon_id = serializers.CharField()
    

class CouponGetSerializer(serializers.Serializer):
    """Used to validate the coupon_id path param before fetching from Zoho."""
    coupon_id = serializers.CharField()


class CouponUpdateSerializer(serializers.Serializer):
    # All fields optional — only include what you want to change
    coupon_name                 = serializers.CharField(required=False)
    coupon_code                = serializers.CharField(required=False, allow_blank=True)
    description                 = serializers.CharField(required=False, allow_blank=True)
    is_active                   = serializers.BooleanField(required=False)
    show_in_storefront          = serializers.BooleanField(required=False)
    discount_value              = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    max_discount_amount         = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    eligible_product_ids        = serializers.ListField(child=serializers.DictField(), required=False)
    valid_from                  = serializers.CharField(required=False)
    never_expires               = serializers.BooleanField(required=False)
    valid_till                  = serializers.CharField(required=False, allow_blank=True)
    minimum_cart_amount_enabled = serializers.BooleanField(required=False)
    minimum_cart_amount         = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    store_limit_enabled         = serializers.BooleanField(required=False)
    store_limit                 = serializers.IntegerField(required=False)
    customer_limit_enabled      = serializers.BooleanField(required=False)
    customer_limit              = serializers.IntegerField(required=False)
`

## offers\services.py

`python
import requests
import json
from decimal import Decimal
from django.contrib.auth import authenticate
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Organization, WebhookConfig


# ── Existing (do not remove) ──────────────────────────────────────────────────

def authenticate_superuser(validated_data: dict) -> dict:
    """
    Business logic for authenticating a superuser.
    Checks credentials, verifies superuser status, and returns JWT tokens.
    """
    email = validated_data.get('email', '').lower()
    password = validated_data.get('password')

    user = authenticate(username=email, password=password)

    if not user:
        raise AuthenticationFailed('Invalid email or password.')

    if not user.is_active:
        raise AuthenticationFailed('This account is inactive.')

    if not user.is_superuser:
        raise AuthenticationFailed('Access denied: User is not a superuser.')

    refresh = RefreshToken.for_user(user)

    return {
        'user': {
            'id': user.id,
            'email': user.email,
            'is_superuser': user.is_superuser,
        },
        'tokens': {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }
    }


# ── Zoho Webhook Service ──────────────────────────────────────────────────────
class _DecimalEncoder(json.JSONEncoder):
    """Converts Decimal values to float for JSON serialization."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj) 
    
class ZohoWebhookService:
    """
    All Zoho Commerce operations go through Zoho Incoming Webhooks.

    Auth model:
    - Django stores the full ZAPI key URL (including encapiKey) in WebhookConfig.
    - Django POSTs JSON to that URL. That is the only thing Django does.
    - The encapiKey in the URL authenticates the request to Zoho.
    - Inside Zoho, the Deluge script uses zoho_commerce_connection (OAuth)
      to call the Commerce API. Django never sees or stores OAuth tokens.

    No client_id, client_secret, or access_token is stored anywhere in Django.
    """

    TIMEOUT_SECONDS = 30

    def _get_webhook_url(self, org_id: int, webhook_type: str) -> str:
        """
        Look up the ZAPI webhook URL for a given organization and action.
        org_id here is the Django DB primary key of the Organization record,
        NOT the Zoho org ID string.
        """
        try:
            org = Organization.objects.get(org_id=org_id, is_active=True)
        except Organization.DoesNotExist:
            raise ValueError(f"Organization id={org_id} not found or inactive.")

        try:
            webhook = org.webhooks.get(webhook_type=webhook_type, is_active=True)
        except WebhookConfig.DoesNotExist:
            raise ValueError(
                f"No active '{webhook_type}' webhook for '{org.name}'. "
                f"Add it in Django admin → WebhookConfig."
            )

        return webhook.webhook_url

    # def _post(self, url: str, payload: dict) -> dict:
    #     """
    #     POST a JSON payload to a Zoho ZAPI webhook URL.
    #     The encapiKey query param in the URL handles authentication.
    #     """
    #     try:
    #         response = requests.post(
    #             url,
    #             json=payload,
    #             headers={"Content-Type": "application/json"},
    #             timeout=self.TIMEOUT_SECONDS
    #         )
    #         response.raise_for_status()
    #         return response.json()
    #     except requests.exceptions.Timeout:
    #         raise ValueError("Zoho webhook timed out (30s).")
    #     except requests.exceptions.ConnectionError:
    #         raise ValueError("Cannot reach Zoho webhook. Check network.")
    #     except requests.exceptions.HTTPError as e:
    #         raise ValueError(f"Zoho returned HTTP error: {str(e)}")
    #     except requests.exceptions.RequestException as e:
    #         raise ValueError(f"Zoho webhook request failed: {str(e)}")
    
    def _post(self, url: str, payload: dict) -> dict:
        """
        POST a JSON payload to a Zoho ZAPI webhook URL.
        Uses a custom encoder to handle Decimal values from DRF serializers.
        """
        try:
            response = requests.post(
                url,
                data=json.dumps(payload, cls=_DecimalEncoder),
                headers={"Content-Type": "application/json"},
                timeout=self.TIMEOUT_SECONDS
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            raise ValueError("Zoho webhook timed out (30s).")
        except requests.exceptions.ConnectionError:
            raise ValueError("Cannot reach Zoho webhook. Check network.")
        except requests.exceptions.HTTPError as e:
            raise ValueError(f"Zoho returned HTTP error: {str(e)}")
        except requests.exceptions.RequestException as e:
            raise ValueError(f"Zoho webhook request failed: {str(e)}")

    def get_organizations(self):
        """Return all active organizations (used by OrganizationListView)."""
        return Organization.objects.filter(is_active=True).prefetch_related('webhooks')

    def list_coupons(self, org_id: int) -> dict:
        """
        POST {} to the list_coupons webhook.
        The webhook runs a GET internally and returns all coupons.
        """
        url = self._get_webhook_url(org_id, 'list_coupons')
        return self._post(url, {})

    def create_coupon(self, org_id: int, coupon_data: dict) -> dict:
        """
        POST validated coupon_data to the create_coupon webhook.
        coupon_data comes from CouponCreateSerializer.validated_data.
        """
        url = self._get_webhook_url(org_id, 'create_coupon')
        return self._post(url, coupon_data)

    def delete_coupon(self, org_id: int, coupon_id: str) -> dict:
        """
        POST {"coupon_id": "..."} to the delete_coupon webhook.
        The webhook runs a DELETE internally against Zoho Commerce.
        """
        url = self._get_webhook_url(org_id, 'delete_coupon')
        return self._post(url, {"coupon_id": coupon_id})
    
    def get_coupon(self, org_id: int, coupon_id: str) -> dict:
        """
        POST {"coupon_id": "..."} to the get_coupon webhook.
        Returns full coupon details from Zoho Commerce.
        Used to pre-fill the edit form on the frontend.
        """
        url = self._get_webhook_url(org_id, 'get_coupon')
        return self._post(url, {"coupon_id": coupon_id})

    def update_coupon(self, org_id: int, coupon_id: str, update_data: dict) -> dict:
        """
        POST {"coupon_id": "...", ...update_fields} to the update_coupon webhook.
        update_data comes from CouponUpdateSerializer.validated_data.
        Only fields present in update_data are sent — Zoho ignores missing fields.
        """
        url = self._get_webhook_url(org_id, 'update_coupon')
        payload = {"coupon_id": coupon_id, **update_data}
        return self._post(url, payload)
`

## offers\tests.py

`python
from django.test import TestCase

# Create your tests here.

`

## offers\urls.py

`python
from django.urls import path
from .views import (
    SuperuserLoginView,
    OrganizationListView,
    ListCouponsView,
    CreateCouponView,
    GetCouponView,
    UpdateCouponView,
    DeleteCouponView,
)

app_name = 'offers'

urlpatterns = [
    # ── Existing ───────────────────────────────────────────────
    path('superuser-login/', SuperuserLoginView.as_view(), name='superuser-login'),

    # ── Organizations ───────────────────────────────────────────
    path('organizations/', OrganizationListView.as_view(), name='org-list'),

    # ── Coupons ─────────────────────────────────────────────────
    path('organizations/<int:org_id>/coupons/', ListCouponsView.as_view(), name='list-coupons'),
    path('organizations/<int:org_id>/coupons/create/', CreateCouponView.as_view(), name='create-coupon'),
    path('organizations/<int:org_id>/coupons/delete/', DeleteCouponView.as_view(), name='delete-coupon'),
    path('organizations/<int:org_id>/coupons/<str:coupon_id>/', GetCouponView.as_view(), name='get-coupon'),
    path('organizations/<int:org_id>/coupons/<str:coupon_id>/update/', UpdateCouponView.as_view(), name='update-coupon'),
]
`

## offers\views.py

`python
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import (
    SuperuserLoginSerializer,
    OrganizationSerializer,
    CouponCreateSerializer,
    CouponUpdateSerializer, 
    CouponGetSerializer,
    CouponDeleteSerializer,
)
from .services import authenticate_superuser, ZohoWebhookService


# ── Existing (do not remove) ──────────────────────────────────────────────────

class SuperuserLoginView(APIView):
    """
    API View to handle Superuser login.
    Allows any user to attempt login, but the service layer strictly enforces superuser-only access.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = SuperuserLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        login_data = authenticate_superuser(serializer.validated_data)

        return Response(
            {
                'message': 'Superuser authenticated successfully.',
                'data': login_data
            },
            status=status.HTTP_200_OK
        )


# ── Organizations ─────────────────────────────────────────────────────────────

class OrganizationListView(APIView):
    """
    GET /api/offers/organizations/
    Returns all active organizations with name, image, org_id.
    Frontend uses this to show the org selection grid.
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        service = ZohoWebhookService()
        orgs = service.get_organizations()
        serializer = OrganizationSerializer(
            orgs, many=True, context={'request': request}
        )
        return Response(
            {'message': 'Organizations fetched successfully.', 'data': serializer.data},
            status=status.HTTP_200_OK
        )


# ── Coupons ───────────────────────────────────────────────────────────────────

class ListCouponsView(APIView):
    """
    GET /api/offers/organizations/<org_id>/coupons/
    Calls the list_coupons webhook for the selected org.
    Returns the coupon list from Zoho Commerce.
    """
    permission_classes = [IsAdminUser]

    def get(self, request, org_id):
        service = ZohoWebhookService()
        try:
            result = service.list_coupons(org_id)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {'message': 'Coupons fetched successfully.', 'data': result},
            status=status.HTTP_200_OK
        )


class CreateCouponView(APIView):
    """
    POST /api/offers/organizations/<org_id>/coupons/create/
    Validates input, then calls the create_coupon webhook.
    Checks Zoho's inner response code before returning success.
    """
    permission_classes = [IsAdminUser]

    def post(self, request, org_id):
        serializer = CouponCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        service = ZohoWebhookService()
        try:
            result = service.create_coupon(org_id, serializer.validated_data)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # The webhook wraps Zoho's response. Check the inner code.
        zoho_response = result.get('response', {}).get('zoho_response', {})
        if zoho_response.get('code') != 0:
            return Response(
                {
                    'error': 'Zoho Commerce rejected the coupon.',
                    'zoho_message': zoho_response.get('message'),
                    'zoho_code': zoho_response.get('code'),
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {'message': 'Coupon created successfully.', 'data': result},
            status=status.HTTP_201_CREATED
        )


class DeleteCouponView(APIView):
    """
    DELETE /api/offers/organizations/<org_id>/coupons/delete/
    Calls the delete_coupon webhook with the given coupon_id.
    Body: {"coupon_id": "3743983000000049109"}
    """
    permission_classes = [IsAdminUser]

    def delete(self, request, org_id):
        serializer = CouponDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        service = ZohoWebhookService()
        try:
            result = service.delete_coupon(
                org_id, serializer.validated_data['coupon_id']
            )
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {'message': 'Coupon deleted successfully.', 'data': result},
            status=status.HTTP_200_OK
        )
        
class GetCouponView(APIView):
    """
    GET /api/offers/organizations/<org_id>/coupons/<coupon_id>/
    Fetches full details of a single coupon from Zoho Commerce.
    Call this before showing the edit form so the client can pre-fill all fields.
    """
    permission_classes = [IsAdminUser]

    # def get(self, request, org_id, coupon_id):
    #     service = ZohoWebhookService()
    #     try:  
    #         result = service.get_coupon(org_id, coupon_id)
    #     except ValueError as e:
    #         return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    #     zoho_response = result.get('zoho_response', {})
    #     # if zoho_response.get('code') != 0:
    #     #     return Response(
    #     #         {
    #     #             'error': 'Zoho Commerce could not fetch the coupon.',
    #     #             'zoho_message': zoho_response.get('message'),
    #     #             'zoho_code': zoho_response.get('code'),
    #     #         },
    #     #         status=status.HTTP_400_BAD_REQUEST
    #     #     )

    #     # return Response(
    #     #     {'message': 'Coupon fetched successfully.', 'data': result},
    #     #     status=status.HTTP_200_OK
    #     # )
    #     if result.get('code') != 0:
    #         return Response(
    #             {
    #                 'error': 'Zoho webhook error.',
    #                 'zoho_message': result.get('message'),
    #             },
    #             status=status.HTTP_400_BAD_REQUEST
    #         )

    #     zoho_response = result.get('zoho_response', {})
    #     if zoho_response.get('code') != 0:
    #         return Response(
    #             {
    #                 'error': 'Zoho Commerce could not fetch the coupon.',
    #                 'zoho_message': zoho_response.get('message'),
    #                 'zoho_code': zoho_response.get('code'),
    #             },
    #             status=status.HTTP_400_BAD_REQUEST
    #         )

    #     return Response(
    #         {'message': 'Coupon fetched successfully.', 'data': result},
    #         status=status.HTTP_200_OK
    #     )
    def get(self, request, org_id, coupon_id):
        service = ZohoWebhookService()
        try:
            result = service.get_coupon(org_id, coupon_id)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # Accept both webhook envelopes:
        # 1) {"code": 0, "zoho_response": {...}}
        # 2) {"response": {"code": 0, "zoho_response": {...}}}
        envelope = result.get('response', result)

        if envelope.get('code') not in (None, 0):
            return Response(
                {
                    'error': 'Zoho webhook error.',
                    'zoho_message': envelope.get('message'),
                    'zoho_code': envelope.get('code'),
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        zoho_response = envelope.get('zoho_response', envelope)

        if not isinstance(zoho_response, dict):
            return Response(
                {
                    'error': 'Invalid Zoho response format.',
                    'zoho_message': 'Expected JSON object for zoho_response.',
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        if zoho_response.get('code') not in (None, 0):
            return Response(
                {
                    'error': 'Zoho Commerce could not fetch the coupon.',
                    'zoho_message': zoho_response.get('message'),
                    'zoho_code': zoho_response.get('code'),
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        coupon = zoho_response.get('coupon', {})

        clean_coupon = {
            "coupon_id": coupon.get("coupon_id"),
            "coupon_code": coupon.get("coupon_code"),
            "coupon_name": coupon.get("coupon_name") or coupon.get("name"),
            "description": coupon.get("description"),
            "discount_type": coupon.get("discount_type"),
            "discount_value": coupon.get("discount_value"),
            "max_discount_amount": coupon.get("max_discount_amount"),
            "status": coupon.get("status"),
            "is_active": coupon.get("is_active"),
            "activation_time": coupon.get("activation_time"),
            "expiry_at": coupon.get("expiry_at"),
            "minimum_order_value": coupon.get("minimum_order_value"),
            "max_redemption_count": coupon.get("max_redemption_count"),
            "max_redemption_count_per_user": coupon.get("max_redemption_count_per_user"),
            "eligible_products": coupon.get("eligible_products", {}),
}

        return Response(
            {'message': 'Coupon fetched successfully.', 'data': clean_coupon},
            status=status.HTTP_200_OK
        )


class UpdateCouponView(APIView):
    """
    PUT /api/offers/organizations/<org_id>/coupons/<coupon_id>/update/
    Validates update fields (all optional), then calls the update_coupon webhook.
    Only fields included in the request body are sent to Zoho.

    Typical edit flow:
      1. GET  /coupons/<coupon_id>/        → pre-fill the form
      2. PUT  /coupons/<coupon_id>/update/ → submit changed fields only

    Body example: {"coupon_name": "New Name", "discount_value": 75}
    """
    permission_classes = [IsAdminUser]

    def put(self, request, org_id, coupon_id):
        serializer = CouponUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        service = ZohoWebhookService()
        try:
            result = service.update_coupon(
                org_id,
                coupon_id,
                serializer.validated_data
            )
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # Accept both webhook envelopes:
        # 1) {"code": 0, "zoho_response": {...}}
        # 2) {"response": {"code": 0, "zoho_response": {...}}}
        envelope = result.get('response', result)

        if envelope.get('code') not in (None, 0):
            return Response(
                {
                    'error': 'Zoho webhook error.',
                    'zoho_message': envelope.get('message'),
                    'zoho_code': envelope.get('code'),
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        zoho_response = envelope.get('zoho_response', envelope)

        if not isinstance(zoho_response, dict):
            return Response(
                {
                    'error': 'Invalid Zoho response format.',
                    'zoho_message': 'Expected JSON object for zoho_response.',
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        if zoho_response.get('code') not in (None, 0):
            return Response(
                {
                    'error': 'Zoho Commerce rejected the update.',
                    'zoho_message': zoho_response.get('message'),
                    'zoho_code': zoho_response.get('code'),
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        coupon = zoho_response.get('coupon', {})

        clean_coupon = {
            "coupon_id": coupon.get("coupon_id"),
            "coupon_code": coupon.get("coupon_code"),
            "coupon_name": coupon.get("coupon_name") or coupon.get("name"),
            "description": coupon.get("description"),
            "discount_type": coupon.get("discount_type"),
            "discount_value": coupon.get("discount_value"),
            "status": coupon.get("status"),
            "is_active": coupon.get("is_active"),
            "minimum_order_value": coupon.get("minimum_order_value"),
            "activation_time": coupon.get("activation_time"),
            "expiry_at": coupon.get("expiry_at"),
            "updated_time": coupon.get("updated_time"),
        }
      
        return Response(
            {'message': 'Coupon updated successfully.', 'data': clean_coupon},
            status=status.HTTP_200_OK
        )
`

## offers\__init__.py

`python

`

## shop\admin.py

`python
from django.contrib import admin
from .models import Cart, CartItem, Order, OrderItem, OrderReturn, OrderReturnLine


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'updated_at')
    search_fields = ('user__email',)
    inlines = [CartItemInline]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('line_total',)


class OrderReturnLineInline(admin.TabularInline):
    model = OrderReturnLine
    extra = 0


@admin.register(OrderReturn)
class OrderReturnAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'user', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('order__id', 'user__email', 'zoho_salesreturn_id')
    inlines = [OrderReturnLineInline]
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'store', 'status', 'total', 'currency', 'zoho_synced_at', 'created_at')
    list_filter = ('status', 'store')
    search_fields = ('user__email', 'shipping_name', 'zoho_salesorder_id')
    inlines = [OrderItemInline]
    readonly_fields = ('created_at', 'updated_at', 'zoho_synced_at', 'zoho_sync_error')

`

## shop\apps.py

`python
from django.apps import AppConfig


class ShopConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'shop'
    verbose_name = 'Shop'

`

## shop\models.py

`python
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum

from catalog.models import Product, Store


class Cart(models.Model):
    """One basket per user; each line carries its ``store`` (multi-store cart)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='carts')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user'], name='shop_cart_user_uniq'),
        ]

    def __str__(self):
        return f'Cart {self.user.email}'


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='+')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='cart_items')
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['cart', 'product'], name='shop_cartitem_cart_product_uniq'),
        ]

    def __str__(self):
        return f'{self.quantity}× {self.product.name}'

    @property
    def line_subtotal(self) -> Decimal:
        return Decimal(self.product.price) * self.quantity


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING_ZOHO_SYNC = 'pending_zoho_sync', 'Pending Zoho sync'
        SYNCED = 'synced', 'Synced'
        SYNC_FAILED = 'sync_failed', 'Zoho sync failed'
        CANCELLED = 'cancelled', 'Cancelled'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='orders')
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='orders')
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING_ZOHO_SYNC,
    )
    currency = models.CharField(max_length=8, default='AED')
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    shipping_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))

    shipping_name = models.CharField(max_length=255)
    shipping_phone = models.CharField(max_length=50)
    shipping_address = models.CharField(max_length=500)
    shipping_city = models.CharField(max_length=120)
    shipping_state = models.CharField(max_length=120, blank=True)
    shipping_postal_code = models.CharField(max_length=32, blank=True)
    shipping_country = models.CharField(max_length=120)

    billing_same_as_shipping = models.BooleanField(default=True)
    billing_name = models.CharField(max_length=255, blank=True)
    billing_phone = models.CharField(max_length=50, blank=True)
    billing_address = models.CharField(max_length=500, blank=True)
    billing_city = models.CharField(max_length=120, blank=True)
    billing_state = models.CharField(max_length=120, blank=True)
    billing_postal_code = models.CharField(max_length=32, blank=True)
    billing_country = models.CharField(max_length=120, blank=True)

    zoho_checkout_id = models.CharField(max_length=255, blank=True)
    zoho_salesorder_id = models.CharField(max_length=120, blank=True)
    zoho_sync_error = models.TextField(blank=True)
    zoho_synced_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Order {self.pk} ({self.status})'


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='order_items')
    product_name = models.CharField(max_length=255)
    sku = models.CharField(max_length=120, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField()
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    zoho_line_item_id = models.CharField(
        max_length=120,
        blank=True,
        help_text='Zoho sales order line id when synced (for sales returns API).',
    )

    class Meta:
        ordering = ['id']

    def __str__(self):
        return self.product_name

    def quantity_in_active_returns(self) -> int:
        agg = self.return_lines.filter(
            order_return__status__in=(
                'pending_zoho',
                'synced',
                'completed',
            ),
        ).aggregate(s=Sum('quantity'))
        return int(agg['s'] or 0)


class OrderReturn(models.Model):
    class Status(models.TextChoices):
        PENDING_ZOHO = 'pending_zoho', 'Pending Zoho sync'
        SYNCED = 'synced', 'Synced to Zoho'
        COMPLETED = 'completed', 'Completed'
        REJECTED = 'rejected', 'Rejected'
        FAILED = 'failed', 'Zoho sync failed'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='returns')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='order_returns',
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING_ZOHO,
    )
    zoho_salesreturn_id = models.CharField(max_length=120, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Return {self.pk} (order {self.order_id})'


class OrderReturnLine(models.Model):
    order_return = models.ForeignKey(OrderReturn, on_delete=models.CASCADE, related_name='lines')
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name='return_lines')
    quantity = models.PositiveIntegerField()

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.quantity}× item {self.order_item_id}'

`

## shop\serializers.py

`python
from decimal import Decimal

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers

from catalog.models import Product, Store

from .models import Cart, CartItem, Order, OrderItem, OrderReturn, OrderReturnLine


class ProductMiniSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            'id', 'name', 'slug', 'category', 'sku', 'price', 'currency', 'image_url',
        )

    def get_image_url(self, obj):
        current = (getattr(obj, 'image_url', '') or '').strip()
        if current:
            return current
        zoho_pid = (getattr(obj, 'zoho_product_id', '') or '').strip()
        store_id = getattr(obj, 'store_id', None)
        if not (zoho_pid and store_id):
            return ''
        request = self.context.get('request')
        path = f'/api/shop/zoho-products/{zoho_pid}/image/?store_id={store_id}'
        return request.build_absolute_uri(path) if request else path


class StoreTinySerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = ('id', 'name', 'slug')


class CartItemSerializer(serializers.ModelSerializer):
    store = StoreTinySerializer(read_only=True)
    product = ProductMiniSerializer(read_only=True)
    product_id = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.filter(is_active=True),
        source='product',
        write_only=True,
    )
    line_subtotal = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ('id', 'store', 'product', 'product_id', 'quantity', 'line_subtotal')

    def get_line_subtotal(self, obj):
        return str(obj.line_subtotal.quantize(Decimal('0.01')))


class CartItemInGroupSerializer(serializers.ModelSerializer):
    """Line inside a store group (store is omitted; it is on the parent group)."""

    product = ProductMiniSerializer(read_only=True)
    line_subtotal = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ('id', 'product', 'quantity', 'line_subtotal')

    def get_line_subtotal(self, obj):
        return str(obj.line_subtotal.quantize(Decimal('0.01')))


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    store_groups = serializers.SerializerMethodField()
    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = ('id', 'items', 'store_groups', 'subtotal', 'updated_at')

    def get_subtotal(self, obj):
        total = sum((item.line_subtotal for item in obj.items.all()), Decimal('0'))
        return str(total.quantize(Decimal('0.01')))

    def get_store_groups(self, obj):
        items = list(obj.items.all())
        if not items:
            return []
        by_store = {}
        for it in items:
            by_store.setdefault(it.store_id, []).append(it)
        for lines in by_store.values():
            lines.sort(key=lambda x: x.pk)
        def sort_key(sid):
            st = by_store[sid][0].store
            return (st.name.lower(), st.pk)

        groups = []
        for sid in sorted(by_store.keys(), key=sort_key):
            lines = by_store[sid]
            store = lines[0].store
            sub = sum((i.line_subtotal for i in lines), Decimal('0'))
            groups.append({
                'store': StoreTinySerializer(store).data,
                'items': CartItemInGroupSerializer(lines, many=True).data,
                'subtotal': str(sub.quantize(Decimal('0.01'))),
            })
        return groups


class CartAddFromZohoAccountSerializer(serializers.Serializer):
    """
    Same flow as store-list + product-list under /zoho/multi/... — uses ZohoCommerceAccount id
    and organization_id from the store list, plus zoho_product_id from product list JSON.
    Optional primary_domain from store list (needed to auto-create local Store if missing).
    """

    zoho_account_id = serializers.IntegerField(min_value=1)
    organization_id = serializers.CharField(max_length=120)
    zoho_product_id = serializers.CharField(max_length=120)
    quantity = serializers.IntegerField(min_value=1, default=1)
    primary_domain = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text='From /zoho/multi/stores/ for this organization (e.g. www.example.com).',
    )

    def validate(self, attrs):
        zoho_product_id = (attrs.get('zoho_product_id') or '').strip()
        if not zoho_product_id:
            raise serializers.ValidationError({'zoho_product_id': 'This field is required.'})
        org = (attrs.get('organization_id') or '').strip()
        if not org:
            raise serializers.ValidationError({'organization_id': 'This field is required.'})
        attrs['zoho_product_id'] = zoho_product_id
        attrs['organization_id'] = org
        attrs['primary_domain'] = (attrs.get('primary_domain') or '').strip()
        return attrs


class CartItemUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CartItem
        fields = ('quantity',)
        extra_kwargs = {'quantity': {'min_value': 1}}


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = (
            'id', 'product', 'product_name', 'sku',
            'unit_price', 'quantity', 'line_total', 'zoho_line_item_id',
        )


def _completed_returns_total(order: Order) -> Decimal:
    total = Decimal('0')
    for ret in order.returns.filter(status=OrderReturn.Status.COMPLETED).prefetch_related(
        'lines__order_item',
    ):
        for line in ret.lines.all():
            total += line.order_item.unit_price * line.quantity
    return total.quantize(Decimal('0.01'))


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    returned_total = serializers.SerializerMethodField()
    balance_remaining = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            'id', 'store', 'status', 'currency', 'subtotal', 'shipping_amount', 'total',
            'shipping_name', 'shipping_phone', 'shipping_address', 'shipping_city',
            'shipping_state', 'shipping_postal_code', 'shipping_country',
            'billing_same_as_shipping',
            'billing_name', 'billing_phone', 'billing_address', 'billing_city',
            'billing_state', 'billing_postal_code', 'billing_country',
            'zoho_checkout_id', 'zoho_salesorder_id',
            'zoho_sync_error', 'zoho_synced_at',
            'returned_total', 'balance_remaining',
            'items', 'created_at', 'updated_at',
        )
        read_only_fields = (
            'status', 'subtotal', 'total', 'zoho_checkout_id', 'zoho_salesorder_id',
            'zoho_sync_error', 'zoho_synced_at',
            'returned_total', 'balance_remaining',
            'created_at', 'updated_at',
        )

    def get_returned_total(self, obj):
        return str(_completed_returns_total(obj))

    def get_balance_remaining(self, obj):
        br = (obj.total - _completed_returns_total(obj)).quantize(Decimal('0.01'))
        if br < Decimal('0'):
            br = Decimal('0')
        return str(br)


class CheckoutSerializer(serializers.Serializer):
    store_id = serializers.IntegerField()
    shipping_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        default=Decimal('0'),
        min_value=Decimal('0'),
    )

    shipping_name = serializers.CharField(max_length=255)
    shipping_phone = serializers.CharField(max_length=50)
    shipping_address = serializers.CharField(max_length=500)
    shipping_city = serializers.CharField(max_length=120)
    shipping_state = serializers.CharField(max_length=120, required=False, allow_blank=True)
    shipping_postal_code = serializers.CharField(max_length=32, required=False, allow_blank=True)
    shipping_country = serializers.CharField(max_length=120)

    billing_same_as_shipping = serializers.BooleanField(default=True)
    billing_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    billing_phone = serializers.CharField(max_length=50, required=False, allow_blank=True)
    billing_address = serializers.CharField(max_length=500, required=False, allow_blank=True)
    billing_city = serializers.CharField(max_length=120, required=False, allow_blank=True)
    billing_state = serializers.CharField(max_length=120, required=False, allow_blank=True)
    billing_postal_code = serializers.CharField(max_length=32, required=False, allow_blank=True)
    billing_country = serializers.CharField(max_length=120, required=False, allow_blank=True)

    def validate(self, attrs):
        store = get_object_or_404(Store, pk=attrs['store_id'], is_active=True)
        attrs['store'] = store

        request = self.context.get('request')
        user = request.user if request else None
        cart = (
            Cart.objects.filter(user=user)
            .prefetch_related('items__product', 'items__store')
            .first()
        )
        if not cart:
            raise serializers.ValidationError({'cart': 'No cart found.'})
        checkout_items = [i for i in cart.items.all() if i.store_id == store.pk]
        if not checkout_items:
            raise serializers.ValidationError({'cart': 'Cart has no items for this store.'})
        attrs['cart'] = cart
        attrs['checkout_items'] = checkout_items

        if not attrs.get('billing_same_as_shipping'):
            required = [
                'billing_name', 'billing_phone', 'billing_address',
                'billing_city', 'billing_country',
            ]
            missing = [f for f in required if not (attrs.get(f) or '').strip()]
            if missing:
                raise serializers.ValidationError(
                    {f: 'Required when billing is not same as shipping.' for f in missing},
                )
        return attrs


class OrderReturnLineInputSerializer(serializers.Serializer):
    order_item_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1)


class OrderReturnCreateSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default='')
    lines = OrderReturnLineInputSerializer(many=True)

    def validate_lines(self, rows):
        if not rows:
            raise serializers.ValidationError('At least one return line is required.')
        seen = set()
        for row in rows:
            oid = row['order_item_id']
            if oid in seen:
                raise serializers.ValidationError('Duplicate order_item_id in request.')
            seen.add(oid)
        return rows

    def validate(self, attrs):
        order: Order = self.context['order']
        for row in attrs['lines']:
            oi = OrderItem.objects.filter(pk=row['order_item_id'], order=order).first()
            if not oi:
                raise serializers.ValidationError(
                    {'lines': f'order_item_id {row["order_item_id"]} is not on this order.'},
                )
            remaining = oi.quantity - oi.quantity_in_active_returns()
            if row['quantity'] > remaining:
                raise serializers.ValidationError(
                    {'lines': f'Quantity exceeds returnable amount for line {oi.pk}.'},
                )
        return attrs

    def create(self, validated_data):
        order = self.context['order']
        user = self.context['request'].user
        lines_data = validated_data['lines']
        note = validated_data.get('note') or ''
        with transaction.atomic():
            ret = OrderReturn.objects.create(order=order, user=user, note=note)
            for row in lines_data:
                oi = OrderItem.objects.get(pk=row['order_item_id'], order=order)
                OrderReturnLine.objects.create(
                    order_return=ret,
                    order_item=oi,
                    quantity=row['quantity'],
                )
        return ret


class OrderReturnLineReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderReturnLine
        fields = ('id', 'order_item', 'quantity')


class OrderReturnReadSerializer(serializers.ModelSerializer):
    lines = OrderReturnLineReadSerializer(many=True, read_only=True)

    class Meta:
        model = OrderReturn
        fields = (
            'id', 'status', 'zoho_salesreturn_id', 'note', 'lines', 'created_at', 'updated_at',
        )

`

## shop\urls.py

`python
from django.urls import path

from .views import (
    CartDetailAPIView,
    CartAddItemAPIView,
    CartItemDetailAPIView,
    CheckoutAPIView,
    OrderListAPIView,
    OrderDetailAPIView,
    OrderReturnListCreateAPIView,
    OrderReorderAPIView,
    ZohoProductListAPIView,
    ZohoProductDetailAPIView,
    ZohoProductImageProxyAPIView,
)

urlpatterns = [
    path('cart/', CartDetailAPIView.as_view(), name='shop-cart'),
    path('cart/items/', CartAddItemAPIView.as_view(), name='shop-cart-add-item'),
    path('cart/items/<int:pk>/', CartItemDetailAPIView.as_view(), name='shop-cart-item'),
    path('orders/checkout/', CheckoutAPIView.as_view(), name='shop-checkout'),
    path('orders/<int:pk>/returns/', OrderReturnListCreateAPIView.as_view(), name='shop-order-returns'),
    path('orders/<int:pk>/reorder/', OrderReorderAPIView.as_view(), name='shop-order-reorder'),
    path('orders/', OrderListAPIView.as_view(), name='shop-order-list'),
    path('orders/<int:pk>/', OrderDetailAPIView.as_view(), name='shop-order-detail'),

    path('zoho-products/', ZohoProductListAPIView.as_view(), name='zoho-product-list'),
    path('zoho-products/<str:product_id>/', ZohoProductDetailAPIView.as_view(), name='zoho-product-detail'),
    path(
        'zoho-products/<str:product_id>/image/',
        ZohoProductImageProxyAPIView.as_view(),
        name='zoho-product-image-proxy',
    ),

]

`

## shop\views.py

`python
from decimal import Decimal
from typing import Optional, Tuple
from urllib.parse import urlparse

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.utils.text import slugify
from catalog.models import Store, Product
from zoho_integration.models import ZohoCommerceAccount
from zoho_integration.services import ZohoCommerceService as ZohoAccountService
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Cart, CartItem, Order, OrderItem, OrderReturn
from .services.zoho_commerce import ZohoCommerceError, ZohoCommerceService
from .serializers import (
    CartSerializer,
    CartAddFromZohoAccountSerializer,
    CartItemSerializer,
    CartItemUpdateSerializer,
    CheckoutSerializer,
    OrderSerializer,
    OrderReturnCreateSerializer,
    OrderReturnReadSerializer,
)
from .services.zoho_returns import enqueue_push_return_to_zoho


def _optional_store_for_zoho(request):
    """
    Optional ``store_id`` query param selects per-store Zoho storefront domain + org.
    When omitted, global ZOHO_STORE_DOMAIN / ZOHO_ORG_ID are used.
    """
    raw = request.query_params.get('store_id')
    if raw is None or str(raw).strip() == '':
        return None, None
    try:
        pk = int(raw)
    except (TypeError, ValueError):
        return None, Response(
            {'detail': 'store_id must be an integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    store = Store.objects.filter(pk=pk).first()
    if not store:
        return None, Response(
            {'detail': 'Store not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    return store, None


def _as_decimal(raw, default='0'):
    try:
        return Decimal(str(raw)).quantize(Decimal('0.01'))
    except Exception:
        return Decimal(default).quantize(Decimal('0.01'))


def _upsert_local_product_from_zoho(store: Store, zoho_product_id: str, payload: dict) -> Product:
    product_blob = payload.get('product') if isinstance(payload, dict) else None
    source = product_blob if isinstance(product_blob, dict) else payload
    if not isinstance(source, dict):
        raise ZohoCommerceError('Invalid product response from Zoho.')

    variants = source.get('variants') if isinstance(source.get('variants'), list) else []
    first_variant = variants[0] if variants and isinstance(variants[0], dict) else {}

    name = str(
        source.get('name')
        or source.get('product_name')
        or source.get('item_name')
        or first_variant.get('name')
        or f'Zoho Product {zoho_product_id}'
    ).strip()
    sku = str(
        source.get('sku')
        or first_variant.get('sku')
        or source.get('product_id')
        or first_variant.get('variant_id')
        or zoho_product_id
        or ''
    ).strip()
    category = str(source.get('category_name') or source.get('category') or '').strip()
    description = str(source.get('description') or '').strip()
    currency = str(source.get('currency_code') or source.get('currency') or 'AED').strip() or 'AED'
    price = _as_decimal(
        source.get('min_rate')
        or source.get('rate')
        or source.get('price')
        or source.get('selling_price')
        or first_variant.get('rate')
        or '0'
    )
    compare_at_price_raw = source.get('regular_price') or source.get('compare_at_price')
    if compare_at_price_raw in (None, ''):
        compare_at_price_raw = first_variant.get('label_rate')
    compare_at_price = (
        _as_decimal(compare_at_price_raw)
        if compare_at_price_raw not in (None, '')
        else None
    )
    docs = source.get('documents') if isinstance(source.get('documents'), list) else []
    first_doc = docs[0] if docs and isinstance(docs[0], dict) else {}
    variant_docs = (
        first_variant.get('documents')
        if isinstance(first_variant.get('documents'), list)
        else []
    )
    first_variant_doc = (
        variant_docs[0]
        if variant_docs and isinstance(variant_docs[0], dict)
        else {}
    )
    image_url = str(
        source.get('image_url')
        or source.get('image_name')
        or source.get('image')
        or source.get('image_path')
        or first_doc.get('image_url')
        or first_doc.get('url')
        or first_doc.get('document_url')
        or first_doc.get('download_url')
        or first_variant_doc.get('image_url')
        or first_variant_doc.get('url')
        or first_variant_doc.get('document_url')
        or first_variant_doc.get('download_url')
        or ''
    ).strip()

    product = Product.objects.filter(store=store, zoho_product_id=zoho_product_id).first()
    base_slug = slugify(name) or f'zoho-{zoho_product_id}'
    slug = base_slug[:255]
    if product is None:
        suffix = 1
        while Product.objects.filter(store=store, slug=slug).exists():
            suffix += 1
            slug = f'{base_slug[:245]}-{suffix}'[:255]
        product = Product(
            store=store,
            zoho_product_id=zoho_product_id,
            slug=slug,
        )

    fallback_name = f'Zoho Product {zoho_product_id}'
    resolved_name = name
    if (
        product.pk
        and ((name or '').strip() == fallback_name)
        and (product.name or '').strip()
        and (product.name or '').strip() != fallback_name
    ):
        # Do not overwrite an existing real name with fallback.
        resolved_name = product.name

    resolved_sku = sku[:120] if sku else (product.sku or '')
    resolved_category = category[:255] if category else (product.category or '')
    resolved_description = description if description else (product.description or '')
    resolved_currency = currency[:8] if currency else (product.currency or 'AED')
    resolved_image_url = image_url[:500] if image_url else (product.image_url or '')

    # Keep existing non-zero price when payload only has fallback 0.
    resolved_price = price
    if product.pk:
        try:
            existing_price = Decimal(str(product.price or '0'))
        except Exception:
            existing_price = Decimal('0')
        if resolved_price <= Decimal('0') and existing_price > Decimal('0'):
            resolved_price = existing_price

    resolved_compare_at_price = compare_at_price
    if resolved_compare_at_price in (None, ''):
        resolved_compare_at_price = product.compare_at_price

    product.name = resolved_name[:255]
    product.sku = resolved_sku
    product.category = resolved_category
    product.description = resolved_description
    product.price = resolved_price
    product.compare_at_price = resolved_compare_at_price
    product.currency = resolved_currency
    product.image_url = resolved_image_url
    product.is_active = True
    product.save()
    return product


def _extract_image_url_from_zoho_payload(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ''
    product_blob = payload.get('product') if isinstance(payload, dict) else None
    source = product_blob if isinstance(product_blob, dict) else payload
    if not isinstance(source, dict):
        return ''
    variants = source.get('variants') if isinstance(source.get('variants'), list) else []
    first_variant = variants[0] if variants and isinstance(variants[0], dict) else {}
    docs = source.get('documents') if isinstance(source.get('documents'), list) else []
    first_doc = docs[0] if docs and isinstance(docs[0], dict) else {}
    variant_docs = (
        first_variant.get('documents')
        if isinstance(first_variant.get('documents'), list)
        else []
    )
    first_variant_doc = (
        variant_docs[0]
        if variant_docs and isinstance(variant_docs[0], dict)
        else {}
    )
    return str(
        source.get('image_url')
        or source.get('image_name')
        or source.get('image')
        or source.get('image_path')
        or first_doc.get('image_url')
        or first_doc.get('url')
        or first_doc.get('document_url')
        or first_doc.get('download_url')
        or first_variant_doc.get('image_url')
        or first_variant_doc.get('url')
        or first_variant_doc.get('document_url')
        or first_variant_doc.get('download_url')
        or ''
    ).strip()


def _normalize_zoho_store_domain(raw: str) -> str:
    s = (raw or '').strip()
    if not s:
        return ''
    if '://' not in s and '/' in s:
        s = s.split('/')[0]
    if '://' in s:
        parsed = urlparse(s)
        host = (parsed.netloc or parsed.path or '').split('/')[0]
    else:
        host = s.split('/')[0]
    return host.strip().lower()


def _resolve_or_create_store_for_zoho_account(
    account: ZohoCommerceAccount,
    organization_id: str,
    primary_domain: str,
) -> Tuple[Optional[Store], Optional[str]]:
    """
    Match local catalog.Store by zoho_org_id, or create one using OAuth fields from
    ZohoCommerceAccount plus primary_domain for zoho_store_domain (domain-name header).
    """
    store = Store.objects.filter(zoho_org_id=organization_id, is_active=True).first()
    if store is not None:
        domain = _normalize_zoho_store_domain(primary_domain)
        if domain and not (store.zoho_store_domain or '').strip():
            store.zoho_store_domain = domain[:255]
            store.save(update_fields=['zoho_store_domain'])
        return store, None

    domain = _normalize_zoho_store_domain(primary_domain)
    if not domain:
        return None, (
            'No local Store for this organization_id. Pass primary_domain from '
            '/zoho/multi/stores/ for this organization, or create a Store in admin with '
            'zoho_org_id and zoho_store_domain set.'
        )

    base_slug = slugify(f'{account.name}-{organization_id}') or f'zoho-org-{organization_id}'
    slug = base_slug[:200]
    n = 0
    while Store.objects.filter(slug=slug).exists():
        n += 1
        slug = f'{base_slug[:190]}-{n}'[:255]

    store = Store.objects.create(
        name=str(account.name)[:255],
        slug=slug,
        zoho_org_id=organization_id[:120],
        zoho_store_domain=domain[:255],
        client_id=(account.client_id or '')[:255],
        client_secret=(account.client_secret or '')[:255],
        refresh_token=account.refresh_token or '',
        is_active=True,
    )
    return store, None


def _fetch_zoho_product_from_account(
    account: ZohoCommerceAccount,
    organization_id: str,
    zoho_product_id: str,
):
    """
    Fetch one Zoho product row from account/org product list response.
    """
    service = ZohoAccountService(account)
    data = service.list_products(organization_id=organization_id, page=1, per_page=200)
    rows = data.get('products', []) or data.get('items', [])
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get('product_id') or row.get('id') or '').strip()
        if pid == zoho_product_id:
            return row
    return None


def _perform_cart_add_zoho_product(
    user,
    store: Store,
    zoho_product_id: str,
    quantity: int,
    *,
    account: Optional[ZohoCommerceAccount] = None,
    organization_id: Optional[str] = None,
):
    """Returns (response_data|None, error_detail|None, http_status)."""
    fresh_zoho_payload = None
    if account is not None and organization_id:
        try:
            fresh_zoho_payload = _fetch_zoho_product_from_account(
                account,
                organization_id,
                zoho_product_id,
            )
        except Exception:
            fresh_zoho_payload = None

    product = Product.objects.filter(
        is_active=True,
        store=store,
        zoho_product_id=zoho_product_id,
    ).first()
    if product is not None and fresh_zoho_payload is not None:
        # Keep local row up-to-date from Zoho list payload on every add.
        product = _upsert_local_product_from_zoho(store, zoho_product_id, fresh_zoho_payload)
    elif product is not None and not (product.sku or '').strip():
        # Backfill legacy rows that were created before SKU fallback existed.
        product.sku = zoho_product_id[:120]
        product.save(update_fields=['sku'])
    if product is None:
        try:
            zoho_payload = fresh_zoho_payload
            if zoho_payload is None:
                zoho_payload = ZohoCommerceService.get_product_detail_storefront(
                    zoho_product_id,
                    store=store,
                )
            product = _upsert_local_product_from_zoho(store, zoho_product_id, zoho_payload)
        except (ZohoCommerceError, Exception) as e:
            return None, str(e), status.HTTP_502_BAD_GATEWAY
    elif not (product.image_url or '').strip():
        # If list payload doesn't include image URL, enrich from detail payload.
        try:
            detail_payload = ZohoCommerceService.get_product_detail_storefront(
                zoho_product_id,
                store=store,
            )
            product = _upsert_local_product_from_zoho(store, zoho_product_id, detail_payload)
        except ZohoCommerceError:
            pass

    with transaction.atomic():
        cart, _ = Cart.objects.select_for_update().get_or_create(user=user)
        item, created = CartItem.objects.select_for_update().get_or_create(
            cart=cart,
            product=product,
            defaults={'quantity': quantity, 'store': store},
        )
        if not created:
            item.quantity += quantity
            item.store = store
            item.save(update_fields=['quantity', 'store'])

    item = CartItem.objects.select_related('product', 'store').get(pk=item.pk)
    return CartItemSerializer(item).data, None, status.HTTP_200_OK


class CartDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cart, _ = Cart.objects.get_or_create(user=request.user)
        cart = (
            Cart.objects.filter(pk=cart.pk)
            .prefetch_related('items__product', 'items__store')
            .first()
        )
        return Response(CartSerializer(cart).data, status=status.HTTP_200_OK)


class CartAddItemAPIView(APIView):
    """
    Add to cart using the same ids as /zoho/multi/stores/ and
    /zoho/multi/accounts/<account_id>/products/<organization_id>/.

    Body: zoho_account_id, organization_id, zoho_product_id, quantity,
    optional primary_domain (from store list for this org — required if no local Store yet).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = CartAddFromZohoAccountSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        account = get_object_or_404(
            ZohoCommerceAccount.objects.filter(is_active=True),
            pk=ser.validated_data['zoho_account_id'],
        )
        organization_id = ser.validated_data['organization_id']
        zoho_product_id = ser.validated_data['zoho_product_id']
        quantity = ser.validated_data['quantity']
        primary_domain = ser.validated_data.get('primary_domain') or ''

        store, resolve_err = _resolve_or_create_store_for_zoho_account(
            account,
            organization_id,
            primary_domain,
        )
        if resolve_err:
            return Response({'detail': resolve_err}, status=status.HTTP_400_BAD_REQUEST)

        data, err, st = _perform_cart_add_zoho_product(
            request.user,
            store,
            zoho_product_id,
            quantity,
            account=account,
            organization_id=organization_id,
        )
        if err:
            return Response({'detail': err}, status=st)
        result = dict(data)
        product_info = result.get('product') or {}
        if isinstance(product_info, dict):
            if not (product_info.get('image_url') or '').strip():
                proxy_url = request.build_absolute_uri(
                    f"/api/shop/zoho-products/{zoho_product_id}/image/?store_id={store.pk}"
                )
                product_info['image_url'] = proxy_url
                result['product'] = product_info
            result['product_name'] = product_info.get('name', '')
            result['sku'] = product_info.get('sku', '')
            result['unit_price'] = product_info.get('price', '0.00')
        result['local_store_id'] = store.pk
        result['line_total'] = result.get('line_subtotal', '0.00')
        result['total_amount'] = result.get('line_subtotal', '0.00')
        return Response(result, status=st)


class CartItemDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CartItemSerializer

    def get_queryset(self):
        return CartItem.objects.filter(cart__user=self.request.user).select_related(
            'product', 'store',
        )

    def get_serializer_class(self):
        if self.request.method in ('PATCH', 'PUT'):
            return CartItemUpdateSerializer
        return CartItemSerializer

    def perform_destroy(self, instance):
        super().perform_destroy(instance)


class CheckoutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = CheckoutSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        cart = ser.validated_data['cart']
        store = ser.validated_data['store']
        items = list(ser.validated_data['checkout_items'])
        if getattr(settings, 'CHECKOUT_TRUST_CLIENT_SHIPPING', False):
            shipping_amount = ser.validated_data.get('shipping_amount') or Decimal('0')
            shipping_amount = Decimal(shipping_amount).quantize(Decimal('0.01'))
        else:
            shipping_amount = Decimal(settings.DEFAULT_SHIPPING_AMOUNT).quantize(Decimal('0.01'))
        subtotal = sum((it.line_subtotal for it in items), Decimal('0'))
        subtotal = subtotal.quantize(Decimal('0.01'))
        total = (subtotal + shipping_amount).quantize(Decimal('0.01'))

        billing_same = ser.validated_data['billing_same_as_shipping']
        ship = {k: ser.validated_data[k] for k in (
            'shipping_name', 'shipping_phone', 'shipping_address', 'shipping_city',
            'shipping_state', 'shipping_postal_code', 'shipping_country',
        )}
        if billing_same:
            bill = {
                'billing_name': ship['shipping_name'],
                'billing_phone': ship['shipping_phone'],
                'billing_address': ship['shipping_address'],
                'billing_city': ship['shipping_city'],
                'billing_state': ship['shipping_state'],
                'billing_postal_code': ship['shipping_postal_code'],
                'billing_country': ship['shipping_country'],
            }
        else:
            bill = {k: ser.validated_data[k] for k in (
                'billing_name', 'billing_phone', 'billing_address', 'billing_city',
                'billing_state', 'billing_postal_code', 'billing_country',
            )}

        currency = items[0].product.currency if items else 'AED'

        with transaction.atomic():
            order = Order.objects.create(
                user=request.user,
                store=store,
                status=Order.Status.PENDING_ZOHO_SYNC,
                currency=currency,
                subtotal=subtotal,
                shipping_amount=shipping_amount,
                total=total,
                billing_same_as_shipping=billing_same,
                **ship,
                **bill,
            )
            for it in items:
                p = it.product
                line = it.line_subtotal.quantize(Decimal('0.01'))
                OrderItem.objects.create(
                    order=order,
                    product=p,
                    product_name=p.name,
                    sku=p.sku,
                    unit_price=p.price,
                    quantity=it.quantity,
                    line_total=line,
                )
            CartItem.objects.filter(pk__in=[i.pk for i in items]).delete()

        order = Order.objects.prefetch_related(
            'items', 'returns__lines__order_item',
        ).get(pk=order.pk)
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)


class OrderListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OrderSerializer

    def get_queryset(self):
        return (
            Order.objects.filter(user=self.request.user)
            .select_related('store')
            .prefetch_related('items', 'returns__lines__order_item')
        )


class OrderDetailAPIView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OrderSerializer

    def get_queryset(self):
        return (
            Order.objects.filter(user=self.request.user)
            .select_related('store')
            .prefetch_related('items', 'returns__lines__order_item')
        )


class OrderReturnListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        order = get_object_or_404(Order, pk=pk, user=request.user)
        qs = order.returns.prefetch_related('lines').order_by('-created_at')
        return Response(OrderReturnReadSerializer(qs, many=True).data)

    def post(self, request, pk):
        order = get_object_or_404(Order, pk=pk, user=request.user)
        ser = OrderReturnCreateSerializer(
            data=request.data,
            context={'order': order, 'request': request},
        )
        ser.is_valid(raise_exception=True)
        ret = ser.save()
        enqueue_push_return_to_zoho(ret.pk)
        ret = OrderReturn.objects.prefetch_related('lines').get(pk=ret.pk)
        return Response(OrderReturnReadSerializer(ret).data, status=status.HTTP_201_CREATED)


class OrderReorderAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        order = get_object_or_404(Order, pk=pk, user=request.user)
        with transaction.atomic():
            cart, _ = Cart.objects.select_for_update().get_or_create(
                user=request.user,
            )
            for oi in order.items.select_related('product'):
                p = oi.product
                if not p or not p.is_active:
                    continue
                st = p.store
                item, created = CartItem.objects.select_for_update().get_or_create(
                    cart=cart,
                    product=p,
                    defaults={'quantity': oi.quantity, 'store': st},
                )
                if not created:
                    item.quantity += oi.quantity
                    item.store = st
                    item.save(update_fields=['quantity', 'store'])
        return Response(
            {'detail': 'Items merged into your cart.', 'store_id': order.store_id},
            status=status.HTTP_200_OK,
        )


class ZohoProductListAPIView(APIView):
    """
    GET — Zoho Commerce storefront product list (proxied JSON for the app).

    Query: ``store_id`` (optional, local Store pk — uses that store's zoho_store_domain / zoho_org_id),
    ``page``, ``per_page``, ``product_type`` (optional).
    When ``store_id`` is omitted, uses ZOHO_STORE_DOMAIN / ZOHO_ORG_ID from settings.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        store, err = _optional_store_for_zoho(request)
        if err:
            return err
        try:
            page = int(request.query_params.get('page', 1))
            per_page = int(request.query_params.get('per_page', 20))
        except (TypeError, ValueError):
            return Response(
                {'detail': 'page and per_page must be integers.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if page < 1:
            return Response({'detail': 'page must be >= 1.'}, status=status.HTTP_400_BAD_REQUEST)
        if per_page < 1 or per_page > 200:
            return Response(
                {'detail': 'per_page must be between 1 and 200.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        product_type = request.query_params.get('product_type') or None
        if product_type is not None:
            product_type = product_type.strip() or None

        try:
            data = ZohoCommerceService.get_products_storefront(
                product_type=product_type,
                page=page,
                per_page=per_page,
                store=store,
            )
            return Response(data, status=status.HTTP_200_OK)
        except ZohoCommerceError as e:
            msg = str(e)
            st = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if ('Set ZOHO' in msg or 'required' in msg.lower())
                else status.HTTP_502_BAD_GATEWAY
            )
            return Response({'detail': msg}, status=st)


class ZohoProductDetailAPIView(APIView):
    """
    GET — Zoho Commerce storefront product detail by Zoho product_id.

    Query: ``store_id`` (optional) — same as list endpoint.
    """

    permission_classes = [AllowAny]

    def get(self, request, product_id):
        store, err = _optional_store_for_zoho(request)
        if err:
            return err
        try:
            data = ZohoCommerceService.get_product_detail_storefront(
                product_id, store=store,
            )
            return Response(data, status=status.HTTP_200_OK)
        except ZohoCommerceError as e:
            msg = str(e)
            if 'required' in msg.lower():
                return Response({'detail': msg}, status=status.HTTP_400_BAD_REQUEST)
            st = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if ('Set ZOHO' in msg or 'domain' in msg.lower())
                else status.HTTP_502_BAD_GATEWAY
            )
            return Response({'detail': msg}, status=st)


class ZohoProductImageProxyAPIView(APIView):
    """
    GET — resolves and redirects to a product image URL when available.
    Query: ``store_id`` (optional) — same as list/detail endpoints.
    """

    permission_classes = [AllowAny]

    def get(self, request, product_id):
        store, err = _optional_store_for_zoho(request)
        if err:
            return err
        try:
            data = ZohoCommerceService.get_product_detail_storefront(
                product_id,
                store=store,
            )
            image_url = _extract_image_url_from_zoho_payload(data)
            if not image_url:
                return Response(
                    {'detail': 'No direct image URL found in Zoho payload for this product.'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return redirect(image_url)
        except ZohoCommerceError as e:
            msg = str(e)
            st = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if ('Set ZOHO' in msg or 'domain' in msg.lower())
                else status.HTTP_502_BAD_GATEWAY
            )
            return Response({'detail': msg}, status=st)
`

## shop\__init__.py

`python

`

## shop\services\cart_zoho.py

`python
from decimal import Decimal
from typing import Optional, Tuple
from urllib.parse import urlparse

from django.db import transaction
from django.utils.text import slugify

from catalog.models import Product, Store
from shop.models import Cart, CartItem
from shop.serializers import CartItemSerializer
from shop.services.zoho_commerce import ZohoCommerceError, ZohoCommerceService
from zoho_integration.models import ZohoCommerceAccount
from zoho_integration.services import ZohoCommerceService as ZohoAccountService


def _as_decimal(raw, default='0'):
    try:
        return Decimal(str(raw)).quantize(Decimal('0.01'))
    except Exception:
        return Decimal(default).quantize(Decimal('0.01'))


def _normalize_zoho_store_domain(raw: str) -> str:
    s = (raw or '').strip()
    if not s:
        return ''
    if '://' not in s and '/' in s:
        s = s.split('/')[0]
    if '://' in s:
        parsed = urlparse(s)
        host = (parsed.netloc or parsed.path or '').split('/')[0]
    else:
        host = s.split('/')[0]
    return host.strip().lower()


def resolve_or_create_store_for_zoho_account(
    account: ZohoCommerceAccount,
    organization_id: str,
    primary_domain: str,
) -> Tuple[Optional[Store], Optional[str]]:
    """
    Match local catalog.Store by zoho_org_id, or create one using OAuth fields from
    ZohoCommerceAccount plus primary_domain for zoho_store_domain (domain-name header).
    """
    store = Store.objects.filter(zoho_org_id=organization_id, is_active=True).first()
    if store is not None:
        domain = _normalize_zoho_store_domain(primary_domain)
        if domain and not (store.zoho_store_domain or '').strip():
            store.zoho_store_domain = domain[:255]
            store.save(update_fields=['zoho_store_domain'])
        return store, None

    domain = _normalize_zoho_store_domain(primary_domain)
    if not domain:
        return None, (
            'No local Store for this organization_id. Pass primary_domain from '
            '/zoho/multi/stores/ for this organization, or create a Store in admin with '
            'zoho_org_id and zoho_store_domain set.'
        )

    base_slug = slugify(f'{account.name}-{organization_id}') or f'zoho-org-{organization_id}'
    slug = base_slug[:200]
    n = 0
    while Store.objects.filter(slug=slug).exists():
        n += 1
        slug = f'{base_slug[:190]}-{n}'[:255]

    store = Store.objects.create(
        name=str(account.name)[:255],
        slug=slug,
        zoho_org_id=organization_id[:120],
        zoho_store_domain=domain[:255],
        client_id=(account.client_id or '')[:255],
        client_secret=(account.client_secret or '')[:255],
        refresh_token=account.refresh_token or '',
        is_active=True,
    )
    return store, None


def _upsert_local_product_from_zoho(store: Store, zoho_product_id: str, payload: dict) -> Product:
    product_blob = payload.get('product') if isinstance(payload, dict) else None
    source = product_blob if isinstance(product_blob, dict) else payload
    if not isinstance(source, dict):
        raise ZohoCommerceError('Invalid product response from Zoho.')

    variants = source.get('variants') if isinstance(source.get('variants'), list) else []
    first_variant = variants[0] if variants and isinstance(variants[0], dict) else {}

    name = str(
        source.get('name')
        or source.get('product_name')
        or source.get('item_name')
        or first_variant.get('name')
        or f'Zoho Product {zoho_product_id}'
    ).strip()
    sku = str(
        source.get('sku')
        or first_variant.get('sku')
        or source.get('product_id')
        or first_variant.get('variant_id')
        or zoho_product_id
        or ''
    ).strip()
    category = str(source.get('category_name') or source.get('category') or '').strip()
    description = str(source.get('description') or '').strip()
    currency = str(source.get('currency_code') or source.get('currency') or 'AED').strip() or 'AED'
    price = _as_decimal(
        source.get('min_rate')
        or source.get('rate')
        or source.get('price')
        or source.get('selling_price')
        or first_variant.get('rate')
        or '0'
    )
    compare_at_price_raw = source.get('regular_price') or source.get('compare_at_price')
    if compare_at_price_raw in (None, ''):
        compare_at_price_raw = first_variant.get('label_rate')
    compare_at_price = (
        _as_decimal(compare_at_price_raw)
        if compare_at_price_raw not in (None, '')
        else None
    )
    docs = source.get('documents') if isinstance(source.get('documents'), list) else []
    first_doc = docs[0] if docs and isinstance(docs[0], dict) else {}
    variant_docs = (
        first_variant.get('documents')
        if isinstance(first_variant.get('documents'), list)
        else []
    )
    first_variant_doc = (
        variant_docs[0]
        if variant_docs and isinstance(variant_docs[0], dict)
        else {}
    )
    image_url = str(
        source.get('image_url')
        or source.get('image_name')
        or source.get('image')
        or source.get('image_path')
        or first_doc.get('image_url')
        or first_doc.get('url')
        or first_doc.get('document_url')
        or first_doc.get('download_url')
        or first_variant_doc.get('image_url')
        or first_variant_doc.get('url')
        or first_variant_doc.get('document_url')
        or first_variant_doc.get('download_url')
        or ''
    ).strip()

    product = Product.objects.filter(store=store, zoho_product_id=zoho_product_id).first()
    base_slug = slugify(name) or f'zoho-{zoho_product_id}'
    slug = base_slug[:255]
    if product is None:
        suffix = 1
        while Product.objects.filter(store=store, slug=slug).exists():
            suffix += 1
            slug = f'{base_slug[:245]}-{suffix}'[:255]
        product = Product(
            store=store,
            zoho_product_id=zoho_product_id,
            slug=slug,
        )

    fallback_name = f'Zoho Product {zoho_product_id}'
    resolved_name = name
    if (
        product.pk
        and ((name or '').strip() == fallback_name)
        and (product.name or '').strip()
        and (product.name or '').strip() != fallback_name
    ):
        resolved_name = product.name

    resolved_sku = sku[:120] if sku else (product.sku or '')
    resolved_category = category[:255] if category else (product.category or '')
    resolved_description = description if description else (product.description or '')
    resolved_currency = currency[:8] if currency else (product.currency or 'AED')
    resolved_image_url = image_url[:500] if image_url else (product.image_url or '')

    resolved_price = price
    if product.pk:
        try:
            existing_price = Decimal(str(product.price or '0'))
        except Exception:
            existing_price = Decimal('0')
        if resolved_price <= Decimal('0') and existing_price > Decimal('0'):
            resolved_price = existing_price

    resolved_compare_at_price = compare_at_price
    if resolved_compare_at_price in (None, ''):
        resolved_compare_at_price = product.compare_at_price

    product.name = resolved_name[:255]
    product.sku = resolved_sku
    product.category = resolved_category
    product.description = resolved_description
    product.price = resolved_price
    product.compare_at_price = resolved_compare_at_price
    product.currency = resolved_currency
    product.image_url = resolved_image_url
    product.is_active = True
    product.save()
    return product


def _fetch_zoho_product_from_account(
    account: ZohoCommerceAccount,
    organization_id: str,
    zoho_product_id: str,
):
    service = ZohoAccountService(account)
    data = service.list_products(organization_id=organization_id, page=1, per_page=200)
    rows = data.get('products', []) or data.get('items', [])
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get('product_id') or row.get('id') or '').strip()
        if pid == zoho_product_id:
            return row
    return None


def perform_cart_add_zoho_product(
    user,
    store: Store,
    zoho_product_id: str,
    quantity: int,
    *,
    account: Optional[ZohoCommerceAccount] = None,
    organization_id: Optional[str] = None,
):
    """Returns (response_data|None, error_detail|None, http_status)."""
    fresh_zoho_payload = None
    if account is not None and organization_id:
        try:
            fresh_zoho_payload = _fetch_zoho_product_from_account(
                account,
                organization_id,
                zoho_product_id,
            )
        except Exception:
            fresh_zoho_payload = None

    product = Product.objects.filter(
        is_active=True,
        store=store,
        zoho_product_id=zoho_product_id,
    ).first()
    if product is not None and fresh_zoho_payload is not None:
        product = _upsert_local_product_from_zoho(store, zoho_product_id, fresh_zoho_payload)
    elif product is not None and not (product.sku or '').strip():
        product.sku = zoho_product_id[:120]
        product.save(update_fields=['sku'])
    if product is None:
        try:
            zoho_payload = fresh_zoho_payload
            if zoho_payload is None:
                zoho_payload = ZohoCommerceService.get_product_detail_storefront(
                    zoho_product_id,
                    store=store,
                )
            product = _upsert_local_product_from_zoho(store, zoho_product_id, zoho_payload)
        except (ZohoCommerceError, Exception) as e:
            return None, str(e), 502
    elif not (product.image_url or '').strip():
        try:
            detail_payload = ZohoCommerceService.get_product_detail_storefront(
                zoho_product_id,
                store=store,
            )
            product = _upsert_local_product_from_zoho(store, zoho_product_id, detail_payload)
        except ZohoCommerceError:
            pass

    with transaction.atomic():
        cart, _ = Cart.objects.select_for_update().get_or_create(user=user)
        item, created = CartItem.objects.select_for_update().get_or_create(
            cart=cart,
            product=product,
            defaults={'quantity': quantity, 'store': store},
        )
        if not created:
            item.quantity += quantity
            item.store = store
            item.save(update_fields=['quantity', 'store'])

    item = CartItem.objects.select_related('product', 'store').get(pk=item.pk)
    return CartItemSerializer(item).data, None, 200

`

## shop\services\order_sync_state.py

`python
"""
Order ↔ Zoho checkout/sales-order sync status transitions.

Use :func:`apply_order_sync_transition` from checkout workers, management commands, or Celery tasks.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone as dj_tz

if TYPE_CHECKING:
    from shop.models import Order


def _order_statuses():
    from shop.models import Order

    return Order.Status


def allowed_transitions(from_status: str) -> frozenset[str]:
    """Valid target ``status`` values for an order currently in ``from_status``."""
    S = _order_statuses()
    pending = S.PENDING_ZOHO_SYNC
    synced = S.SYNCED
    failed = S.SYNC_FAILED
    cancelled = S.CANCELLED
    valid: dict[str, frozenset[str]] = {
        pending: frozenset({synced, failed, cancelled}),
        failed: frozenset({pending, synced, cancelled}),
        synced: frozenset({cancelled}),
        cancelled: frozenset(),
    }
    return valid.get(from_status, frozenset())


def apply_order_sync_transition(
    order: Order,
    new_status: str,
    *,
    error_message: str | None = None,
    zoho_checkout_id: str | None = None,
    zoho_salesorder_id: str | None = None,
    clear_error: bool = False,
) -> None:
    """
    Move ``order`` to ``new_status`` if the transition is allowed; persist Zoho ids and
    sync metadata. Raises ``ValueError`` if the transition is invalid.
    """
    S = _order_statuses()
    cur = order.status
    if new_status not in allowed_transitions(cur):
        raise ValueError(
            f'Cannot transition order {order.pk} from {cur!r} to {new_status!r}.',
        )

    order.status = new_status
    order.updated_at = dj_tz.now()
    update_fields = ['status', 'updated_at']

    if new_status == S.SYNCED:
        order.zoho_sync_error = ''
        order.zoho_synced_at = dj_tz.now()
        update_fields.extend(['zoho_sync_error', 'zoho_synced_at'])
    elif clear_error:
        order.zoho_sync_error = ''
        update_fields.append('zoho_sync_error')
    elif error_message:
        order.zoho_sync_error = str(error_message)[:5000]
        update_fields.append('zoho_sync_error')

    if zoho_checkout_id is not None:
        order.zoho_checkout_id = zoho_checkout_id[:255]
        if 'zoho_checkout_id' not in update_fields:
            update_fields.append('zoho_checkout_id')
    if zoho_salesorder_id is not None:
        order.zoho_salesorder_id = zoho_salesorder_id[:120]
        if 'zoho_salesorder_id' not in update_fields:
            update_fields.append('zoho_salesorder_id')

    with transaction.atomic():
        order.save(update_fields=list(dict.fromkeys(update_fields)))

`

## shop\services\zoho_commerce.py

`python
"""
Zoho Commerce **store** API helpers for shop flows (sales orders, checkout sync, etc.).

Static token (urllib helpers):
  - ZOHO_ACCESS_TOKEN
  - ZOHO_COMMERCE_ORGANIZATION_ID (header X-com-zoho-store-organizationid)

:class:`ZohoCommerceService` uses refresh-token OAuth + storefront ``domain-name`` header.
Django settings: ZOHO_ACCOUNTS_URL, ZOHO_CLIENT_*, ZOHO_REFRESH_TOKEN, ZOHO_STORE_DOMAIN,
ZOHO_ORG_ID (falls back to ZOHO_COMMERCE_ORGANIZATION_ID), ZOHO_COMMERCE_BASE_URL.

Paths for urllib_helpers are under ``/store/api/v1/`` — e.g. ``"salesorders"`` or ``"checkouts"``.
"""
from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import requests
from django.conf import settings
from django.utils import timezone as django_timezone

from catalog.models import Store

STORE_API_PREFIX = '/store/api/v1'


class ZohoCommerceError(Exception):
    """Missing configuration or network failure before a response is available."""


def commerce_base_url() -> str:
    return (os.environ.get('ZOHO_COMMERCE_BASE_URL') or 'https://commerce.zoho.com').rstrip('/')


def commerce_store_api_configured() -> bool:
    token = (os.environ.get('ZOHO_ACCESS_TOKEN') or '').strip()
    org = (os.environ.get('ZOHO_COMMERCE_ORGANIZATION_ID') or '').strip()
    return bool(token and org)


def _auth_headers(
    *,
    extra: dict[str, str] | None = None,
    content_type: str | None = None,
) -> dict[str, str]:
    token = (os.environ.get('ZOHO_ACCESS_TOKEN') or '').strip()
    org_id = (os.environ.get('ZOHO_COMMERCE_ORGANIZATION_ID') or '').strip()
    if not token or not org_id:
        raise ZohoCommerceError(
            'Set ZOHO_ACCESS_TOKEN and ZOHO_COMMERCE_ORGANIZATION_ID for Zoho Commerce.',
        )
    h: dict[str, str] = {
        'Authorization': f'Zoho-oauthtoken {token}',
        'X-com-zoho-store-organizationid': org_id,
    }
    if content_type:
        h['Content-Type'] = content_type
    if extra:
        h.update(extra)
    return h


def commerce_store_url(resource: str, query: dict[str, Any] | None = None) -> str:
    resource = (resource or '').strip().lstrip('/')
    base = f'{commerce_base_url()}{STORE_API_PREFIX}/{resource}'
    if query:
        items = [(k, v) for k, v in query.items() if v is not None and v != '']
        if items:
            return f'{base}?{urlencode(items, doseq=True)}'
    return base


def commerce_store_request(
    method: str,
    resource: str,
    *,
    query: dict[str, Any] | None = None,
    json_data: dict[str, Any] | list[Any] | None = None,
    timeout: int = 60,
) -> tuple[int, Any]:
    """
    Call Zoho Commerce store API. Returns (http_status, body) where body is parsed JSON
    when possible, otherwise the raw response string.
    """
    url = commerce_store_url(resource, query=query)
    m = (method or 'GET').upper()
    data_bytes: bytes | None = None
    headers: dict[str, str]
    if m in ('POST', 'PUT', 'PATCH') and json_data is not None:
        data_bytes = json.dumps(json_data).encode('utf-8')
        headers = _auth_headers(content_type='application/json;charset=UTF-8')
    else:
        headers = _auth_headers()
    req = Request(url, data=data_bytes, headers=headers, method=m)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8')
            status = getattr(resp, 'status', 200) or 200
    except HTTPError as e:
        status = e.code
        raw = e.read().decode('utf-8', errors='replace')
    except URLError as e:
        raise ZohoCommerceError(f'Could not reach Zoho Commerce: {e}') from e

    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def commerce_store_get(
    resource: str,
    *,
    query: dict[str, Any] | None = None,
    timeout: int = 60,
) -> tuple[int, Any]:
    return commerce_store_request('GET', resource, query=query, timeout=timeout)


def commerce_store_post(
    resource: str,
    json_data: dict[str, Any] | list[Any],
    *,
    query: dict[str, Any] | None = None,
    timeout: int = 60,
) -> tuple[int, Any]:
    return commerce_store_request(
        'POST', resource, query=query, json_data=json_data, timeout=timeout,
    )


class ZohoCommerceService:
    """OAuth refresh + Commerce admin headers + storefront product APIs (requests)."""

    @staticmethod
    def _refresh_with_creds(
        *,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> tuple[str, int | None]:
        url = f'{settings.ZOHO_ACCOUNTS_URL}/oauth/v2/token'
        params = {
            'refresh_token': refresh_token,
            'client_id': client_id,
            'client_secret': client_secret,
            'grant_type': 'refresh_token',
        }
        try:
            response = requests.post(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            raise ZohoCommerceError(f'Zoho token refresh request failed: {e}') from e
        except ValueError as e:
            raise ZohoCommerceError('Invalid JSON from Zoho token endpoint.') from e

        access_token = data.get('access_token')
        if not access_token:
            raise ZohoCommerceError(f'Failed to refresh Zoho token: {data}')
        expires_in = data.get('expires_in')
        try:
            exp_secs = int(expires_in) if expires_in is not None else None
        except (TypeError, ValueError):
            exp_secs = None
        return access_token, exp_secs

    @classmethod
    def refresh_access_token(cls, store: Store | None = None) -> str:
        if store is not None:
            rt = (store.refresh_token or '').strip()
            cid = (store.client_id or '').strip()
            cs = (store.client_secret or '').strip()
            if rt and cid and cs:
                access_token, exp_secs = cls._refresh_with_creds(
                    refresh_token=rt,
                    client_id=cid,
                    client_secret=cs,
                )
                if exp_secs is not None:
                    store.token_expiry = django_timezone.now() + timedelta(seconds=exp_secs)
                else:
                    store.token_expiry = None
                store.access_token = access_token
                store.save(update_fields=['access_token', 'token_expiry'])
                return access_token

        if not (
            getattr(settings, 'ZOHO_REFRESH_TOKEN', '')
            and getattr(settings, 'ZOHO_CLIENT_ID', '')
            and getattr(settings, 'ZOHO_CLIENT_SECRET', '')
        ):
            raise ZohoCommerceError(
                'Set ZOHO_REFRESH_TOKEN, ZOHO_CLIENT_ID, and ZOHO_CLIENT_SECRET for token refresh, '
                'or set per-store refresh_token, client_id, and client_secret on the Store.',
            )
        access_token, _exp = cls._refresh_with_creds(
            refresh_token=settings.ZOHO_REFRESH_TOKEN,
            client_id=settings.ZOHO_CLIENT_ID,
            client_secret=settings.ZOHO_CLIENT_SECRET,
        )
        return access_token

    @classmethod
    def admin_headers(cls, store: Store | None = None) -> dict[str, str]:
        org = ''
        if store is not None:
            org = (getattr(store, 'zoho_org_id', '') or '').strip()
        if not org:
            org = (getattr(settings, 'ZOHO_ORG_ID', '') or '').strip()
        if not org:
            raise ZohoCommerceError(
                'Set zoho_org_id on the Store, or ZOHO_ORG_ID / '
                'ZOHO_COMMERCE_ORGANIZATION_ID for admin API requests.',
            )
        access_token = cls.refresh_access_token(store)
        return {
            'Authorization': f'Zoho-oauthtoken {access_token}',
            'X-com-zoho-store-organizationid': org,
        }

    @staticmethod
    def storefront_headers(store: Store | None = None) -> dict[str, str]:
        domain = ''
        if store is not None:
            domain = (getattr(store, 'zoho_store_domain', '') or '').strip()
        if not domain:
            domain = (getattr(settings, 'ZOHO_STORE_DOMAIN', '') or '').strip()
        if not domain:
            raise ZohoCommerceError(
                'Set zoho_store_domain on the Store, or ZOHO_STORE_DOMAIN in settings '
                '(e.g. yourstore.zohostore.com) for storefront API.',
            )
        return {'domain-name': domain}

    @classmethod
    def get_products_storefront(
        cls,
        product_type: str | None = None,
        page: int = 1,
        per_page: int = 20,
        *,
        store: Store | None = None,
    ) -> Any:
        url = f'{settings.ZOHO_COMMERCE_BASE_URL}/storefront/api/v1/products'
        params: dict[str, Any] = {
            'page': page,
            'per_page': per_page,
            'format': 'json',
        }
        if product_type:
            params['product_type'] = product_type
        try:
            response = requests.get(
                url,
                headers=cls.storefront_headers(store),
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise ZohoCommerceError(f'Zoho storefront products request failed: {e}') from e
        except ValueError as e:
            raise ZohoCommerceError('Invalid JSON from Zoho storefront.') from e

    @classmethod
    def get_product_detail_storefront(
        cls, product_id: str, *, store: Store | None = None,
    ) -> Any:
        pid = (product_id or '').strip()
        if not pid:
            raise ZohoCommerceError('product_id is required.')
        url = f'{settings.ZOHO_COMMERCE_BASE_URL}/storefront/api/v1/products/{pid}'
        try:
            response = requests.get(
                url,
                headers=cls.storefront_headers(store),
                params={'format': 'json'},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise ZohoCommerceError(f'Zoho storefront product detail failed: {e}') from e
        except ValueError as e:
            raise ZohoCommerceError('Invalid JSON from Zoho storefront.') from e


__all__ = [
    'ZohoCommerceError',
    'ZohoCommerceService',
    'commerce_base_url',
    'commerce_store_api_configured',
    'commerce_store_get',
    'commerce_store_post',
    'commerce_store_request',
    'commerce_store_url',
]

`

## shop\services\zoho_returns.py

`python
"""
Stub: call Zoho Commerce sales return API when OAuth + order line ids are available.
"""


def enqueue_push_return_to_zoho(order_return_id: int) -> None:
    del order_return_id
    return None

`

## shop\services\__init__.py

`python


`

## zoho_integration\admin.py

`python
from django.contrib import admin

from .models import ZohoCommerceAccount


@admin.register(ZohoCommerceAccount)
class ZohoCommerceAccountAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'email', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'email')
    readonly_fields = ('created_at',)

`

## zoho_integration\apps.py

`python
from django.apps import AppConfig


class ZohoIntegrationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'zoho_integration'

`

## zoho_integration\models.py

`python
from django.db import models


class ZohoCommerceAccount(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    organization_id = models.CharField(max_length=100, null=True, blank=True)
    accounts_url = models.URLField(default='https://accounts.zoho.com')
    commerce_base_url = models.URLField(default='https://commerce.zoho.com')

    client_id = models.CharField(max_length=255)
    client_secret = models.CharField(max_length=255)
    refresh_token = models.TextField()

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.email})'

`

## zoho_integration\services.py

`python
import requests


class ZohoIntegrationError(Exception):
    pass


def get_zoho_access_token(account):
    accounts_url = (getattr(account, "accounts_url", "") or "https://accounts.zoho.com").rstrip("/")
    url = f"{accounts_url}/oauth/v2/token"
    payload = {
        "refresh_token": getattr(account, "refresh_token", ""),
        "client_id": getattr(account, "client_id", ""),
        "client_secret": getattr(account, "client_secret", ""),
        "grant_type": "refresh_token",
    }

    try:
        response = requests.post(url, data=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        raise ZohoIntegrationError(f"Zoho token request failed: {e}") from e
    except ValueError as e:
        raise ZohoIntegrationError("Invalid JSON from Zoho token endpoint.") from e

    token = data.get("access_token")
    if not token:
        raise ZohoIntegrationError(f"Failed to get access token: {data}")
    return token


def get_all_zoho_stores(account):
    access_token = get_zoho_access_token(account)

    base_url = (getattr(account, "commerce_base_url", "") or "https://commerce.zoho.com").rstrip("/")
    url = f"{base_url}/zs-site/api/v1/index/sites"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Accept": "application/json",
    }
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        raise ZohoIntegrationError(f"Zoho stores request failed: {e}") from e
    except ValueError as e:
        raise ZohoIntegrationError("Invalid JSON from Zoho stores endpoint.") from e


class ZohoCommerceService:
    def __init__(self, account):
        self.account = account
        self.accounts_url = account.accounts_url.rstrip("/")
        self.commerce_base_url = account.commerce_base_url.rstrip("/")

    def get_access_token(self):
        return get_zoho_access_token(self.account)

    def _headers(self):
        token = self.get_access_token()
        return {
            "Authorization": f"Zoho-oauthtoken {token}",
            "Content-Type": "application/json",
        }

    def list_stores(self):
        return get_all_zoho_stores(self.account)

    def list_products(self, organization_id, page=1, per_page=200):
        # Products/variants docs indicate store APIs use items READ scope.
        # Some endpoints need organization_id in query params.
        url = f"{self.commerce_base_url}/store/api/v1/products"
        params = {
            "organization_id": organization_id,
            "page": page,
            "per_page": per_page,
        }
        try:
            response = requests.get(url, headers=self._headers(), params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise ZohoIntegrationError(f"Zoho products request failed: {e}") from e
        except ValueError as e:
            raise ZohoIntegrationError("Invalid JSON from Zoho products endpoint.") from e

`

## zoho_integration\urls.py

`python
from django.urls import path
from .views import (
    zoho_callback,
    MultiAccountZohoStoreListAPIView,
    MultiAccountZohoProductListAPIView,
)

urlpatterns = [
    path("callback/", zoho_callback),
    path("multi/stores/", MultiAccountZohoStoreListAPIView.as_view()),
    path("multi/accounts/<int:account_id>/products/<str:organization_id>/", MultiAccountZohoProductListAPIView.as_view()),
]
`

## zoho_integration\views.py

`python
from django.http import JsonResponse
from django.conf import settings
import requests
from typing import Optional
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import ZohoCommerceAccount
from .services import ZohoCommerceService


def _mask_token(value: Optional[str]) -> str:
    token = (value or "").strip()
    if not token:
        return ""
    if len(token) <= 12:
        return f"{token[:3]}***"
    return f"{token[:6]}...{token[-6:]}"


def zoho_callback(request):
    code = request.GET.get("code")
    location = request.GET.get("location")
    accounts_server = request.GET.get("accounts-server")
    account_id = (request.GET.get("account_id") or "").strip()

    if not code:
        return JsonResponse({
            "status": "error",
            "message": "No authorization code received",
            "query_params": dict(request.GET),
        }, status=400)

    account = None
    if account_id:
        try:
            account = ZohoCommerceAccount.objects.get(id=int(account_id), is_active=True)
        except (TypeError, ValueError, ZohoCommerceAccount.DoesNotExist):
            return JsonResponse({
                "status": "error",
                "message": "Invalid account_id or account not found",
            }, status=400)

    accounts_base = (
        account.accounts_url
        if account is not None
        else getattr(settings, "ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com")
    ).rstrip("/")
    client_id = (
        account.client_id
        if account is not None
        else getattr(settings, "ZOHO_CLIENT_ID", "")
    )
    client_secret = (
        account.client_secret
        if account is not None
        else getattr(settings, "ZOHO_CLIENT_SECRET", "")
    )
    redirect_uri = getattr(settings, "ZOHO_REDIRECT_URI", "").strip()
    token_url = f"{accounts_base}/oauth/v2/token"

    if not client_id or not client_secret:
        return JsonResponse({
            "status": "error",
            "message": "Missing Zoho client credentials. Configure account credentials or .env values.",
        }, status=400)

    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }

    try:
        response = requests.post(token_url, data=payload, timeout=30)
        raw_text = response.text

        try:
            token_data = response.json()
        except ValueError:
            token_data = {"non_json_response": raw_text}

        if not response.ok or "error" in token_data:
            return JsonResponse({
                "status": "error",
                "message": "Zoho token exchange failed",
                "http_status": response.status_code,
                "token_url": token_url,
                "request_payload_preview": {
                    "grant_type": payload["grant_type"],
                    "client_id": f"{client_id[:8]}..." if client_id else "",
                    "redirect_uri": payload["redirect_uri"],
                    "code_preview": code[:10] + "...",
                },
                "response_data": token_data,
                "account_id": account.id if account else None,
                "location": location,
                "accounts_server": accounts_server,
            }, status=400)

        if account is not None and token_data.get("refresh_token"):
            account.refresh_token = token_data.get("refresh_token")
            account.save(update_fields=["refresh_token"])

        return JsonResponse({
            "status": "success",
            "message": "Zoho token generated successfully",
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "expires_in": token_data.get("expires_in"),
            "scope": token_data.get("scope"),
            "api_domain": token_data.get("api_domain"),
            "token_type": token_data.get("token_type"),
            "account_id": account.id if account else None,
            "location": location,
            "accounts_server": accounts_server,
        })

    except requests.RequestException as e:
        return JsonResponse({
            "status": "error",
            "message": "Request to Zoho failed",
            "details": str(e),
        }, status=500)

        
def get_zoho_access_token():
    url = f"{settings.ZOHO_ACCOUNTS_URL}/oauth/v2/token"
    payload = {
        "refresh_token": settings.ZOHO_REFRESH_TOKEN,
        "client_id": settings.ZOHO_CLIENT_ID,
        "client_secret": settings.ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }

    response = requests.post(url, data=payload, timeout=30)
    response.raise_for_status()
    return response.json()["access_token"]
def get_all_zoho_stores():
    access_token = get_zoho_access_token()

    url = f"{settings.ZOHO_COMMERCE_BASE_URL}/zs-site/api/v1/index/sites"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Accept": "application/json",
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def zoho_debug_sites(request):
    """
    Temporary diagnostics endpoint for Zoho refresh + sites listing.
    Returns sanitized/masked values only.
    """
    token_url = f"{settings.ZOHO_ACCOUNTS_URL}/oauth/v2/token"
    payload = {
        "refresh_token": settings.ZOHO_REFRESH_TOKEN,
        "client_id": settings.ZOHO_CLIENT_ID,
        "client_secret": settings.ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }

    override_base = (request.GET.get("base_url") or "").strip().rstrip("/")
    base_url = override_base or getattr(settings, "ZOHO_COMMERCE_BASE_URL", "")

    debug = {
        "status": "error",
        "config": {
            "accounts_url": getattr(settings, "ZOHO_ACCOUNTS_URL", ""),
            "commerce_base_url": base_url,
            "commerce_base_url_from_query": bool(override_base),
            "client_id_masked": _mask_token(getattr(settings, "ZOHO_CLIENT_ID", "")),
            "refresh_token_masked": _mask_token(getattr(settings, "ZOHO_REFRESH_TOKEN", "")),
        },
    }

    try:
        token_resp = requests.post(token_url, data=payload, timeout=30)
    except requests.RequestException as e:
        debug["message"] = "Failed to call Zoho token endpoint"
        debug["token_refresh"] = {"error": str(e)}
        return JsonResponse(debug, status=502)

    token_body_text = (token_resp.text or "").strip()
    token_body_preview = token_body_text[:800] if token_body_text else ""
    token_data = {}
    try:
        token_data = token_resp.json()
    except ValueError:
        token_data = {}

    access_token = (token_data.get("access_token") or "").strip()
    debug["token_refresh"] = {
        "http_status": token_resp.status_code,
        "ok": token_resp.ok,
        "scope": token_data.get("scope"),
        "expires_in": token_data.get("expires_in"),
        "token_type": token_data.get("token_type"),
        "access_token_masked": _mask_token(access_token),
        "body_preview": token_body_preview,
    }

    if not token_resp.ok or not access_token:
        debug["message"] = "Refresh token exchange failed"
        return JsonResponse(debug, status=400)

    if not (str(base_url).startswith("http://") or str(base_url).startswith("https://")):
        debug["message"] = "Invalid base_url. Must start with http:// or https://"
        return JsonResponse(debug, status=400)

    sites_url = f"{base_url}/zs-site/api/v1/index/sites"
    sites_headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Accept": "application/json",
    }
    try:
        sites_resp = requests.get(sites_url, headers=sites_headers, timeout=30)
    except requests.RequestException as e:
        debug["message"] = "Token refreshed but sites endpoint request failed"
        debug["sites_call"] = {"error": str(e)}
        return JsonResponse(debug, status=502)

    sites_body_text = (sites_resp.text or "").strip()
    sites_body_preview = sites_body_text[:800] if sites_body_text else ""
    sites_data = {}
    try:
        sites_data = sites_resp.json()
    except ValueError:
        sites_data = {}

    debug["sites_call"] = {
        "http_status": sites_resp.status_code,
        "ok": sites_resp.ok,
        "url": sites_url,
        "body_preview": sites_body_preview,
    }

    if sites_resp.ok:
        my_sites = (
            (sites_data.get("get_sites") or {}).get("my_sites")
            if isinstance(sites_data, dict)
            else None
        )
        debug["status"] = "success"
        debug["message"] = "Zoho token refresh and sites call succeeded"
        debug["result"] = {
            "site_count": len(my_sites) if isinstance(my_sites, list) else 0,
            "domains": [
                s.get("primary_domain", "")
                for s in my_sites
                if isinstance(s, dict)
            ] if isinstance(my_sites, list) else [],
        }
        return JsonResponse(debug, status=200)

    debug["message"] = "Zoho sites call failed after successful token refresh"
    return JsonResponse(debug, status=400)


class MultiAccountZohoStoreListAPIView(APIView):
    def get(self, request):
        accounts = ZohoCommerceAccount.objects.filter(is_active=True)

        result = []
        errors = []

        for account in accounts:
            service = ZohoCommerceService(account)
            try:
                data = service.list_stores()

                # Zoho returns sites under get_sites.my_sites (see zs-site index API).
                stores = []
                if isinstance(data, dict):
                    gs = data.get("get_sites") or {}
                    if isinstance(gs, dict):
                        my_sites = gs.get("my_sites")
                        if isinstance(my_sites, list):
                            stores = [s for s in my_sites if isinstance(s, dict)]
                    if not stores:
                        raw = data.get("sites") or data.get("stores") or []
                        stores = [s for s in raw if isinstance(s, dict)]
                for store in stores:
                    result.append({
                        "account_name": account.name,
                        "account_email": account.email,
                        "store_id": store.get("zsite_id") or store.get("store_id"),
                        "site_name": store.get("site_title") or store.get("site_name"),
                        "primary_domain": store.get("primary_domain") or store.get("domain"),
                        "organization_id": store.get("zohofinance_orgid") or store.get("organization_id"),
                    })
            except Exception as e:
                errors.append({
                    "account_name": account.name,
                    "account_email": account.email,
                    "error": str(e),
                })

        return Response({
            "status": "success",
            "count": len(result),
            "stores": result,
            "errors": errors,
        }, status=status.HTTP_200_OK)


class MultiAccountZohoProductListAPIView(APIView):
    def get(self, request, account_id, organization_id):
        try:
            account = ZohoCommerceAccount.objects.get(id=account_id, is_active=True)
        except ZohoCommerceAccount.DoesNotExist:
            return Response({
                "status": "error",
                "message": "Zoho account not found"
            }, status=404)

        service = ZohoCommerceService(account)

        try:
            data = service.list_products(organization_id=organization_id)
            products = data.get("products", []) or data.get("items", [])

            return Response({
                "status": "success",
                "account_name": account.name,
                "account_email": account.email,
                "organization_id": organization_id,
                "count": len(products),
                "products": products,
            })
        except Exception as e:
            return Response({
                "status": "error",
                "message": str(e),
            }, status=400)
`

## zoho_integration\__init__.py

`python


`

