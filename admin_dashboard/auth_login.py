"""Admin dashboard login helpers (optional per-user MFA via email OTP)."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from rest_framework import status
from rest_framework.response import Response

from .models import AdminLoginOTP

logger = logging.getLogger(__name__)


def admin_login_requires_mfa(user) -> bool:
    """True when this admin must complete email OTP before receiving JWT tokens."""
    if getattr(settings, 'ADMIN_LOGIN_SKIP_OTP', False):
        return False
    return bool(getattr(user, 'admin_mfa_enabled', False))


def send_admin_login_otp(user) -> tuple[bool, Response | None]:
    """
    Invalidate prior OTPs, create a new one, and email it to the admin user.
    Returns (success, error_response).
    """
    to_email = (user.email or '').strip().lower()
    if not to_email or '@' not in to_email:
        return False, Response(
            {'detail': 'Account email is invalid.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    AdminLoginOTP.objects.filter(user=user, is_used=False).update(is_used=True)
    otp = AdminLoginOTP.objects.create(user=user)
    subject = 'AoneGt Admin Login Verification'
    message = (
        f'Your admin login verification code is: {otp.otp_code}\n'
        'This code expires in 10 minutes.\n\n'
        'If you did not attempt to log in, ignore this email.'
    )
    try:
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=False)
    except Exception as exc:
        otp.delete()
        logger.exception('admin-login: SMTP failed (%s)', exc)
        return False, Response(
            {'detail': 'Could not send verification email. Try again later.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return True, None
