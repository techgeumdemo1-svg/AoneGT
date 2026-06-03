"""Remove stale OTP rows from the database."""

import logging

from django.db import connection
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

from accounts.models import (
    AccountDeactivateOTP,
    AccountDeleteOTP,
    AccountReactivateOTP,
    ChangePasswordOTP,
    PasswordResetOTP,
    RegistrationOTP,
)
from admin_dashboard.models import AdminLoginOTP

OTP_MODELS = (
    PasswordResetOTP,
    RegistrationOTP,
    AccountDeactivateOTP,
    AccountDeleteOTP,
    AccountReactivateOTP,
    ChangePasswordOTP,
    AdminLoginOTP,
)


def purge_otps(*, include_used=False, dry_run=False):
    """
    Delete expired OTP rows (expires_at in the past).

    If include_used is True, also delete rows marked is_used=True.
    Returns a dict mapping model class name to deleted row count.
    """
    now = timezone.now()
    counts = {}

    for model in OTP_MODELS:
        table = model._meta.db_table
        if table not in connection.introspection.table_names():
            logger.warning('otp-purge: skip %s (table %s missing)', model.__name__, table)
            counts[model.__name__] = 0
            continue
        if include_used:
            qs = model.objects.filter(Q(expires_at__lt=now) | Q(is_used=True))
        else:
            qs = model.objects.filter(expires_at__lt=now)
        if dry_run:
            counts[model.__name__] = qs.count()
        else:
            deleted, _ = qs.delete()
            counts[model.__name__] = deleted

    return counts
