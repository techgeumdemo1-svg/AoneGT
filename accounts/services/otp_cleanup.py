"""Remove stale OTP rows from the database."""

from django.utils import timezone

from accounts.models import (
    AccountDeactivateOTP,
    AccountDeleteOTP,
    AccountReactivateOTP,
    ChangePasswordOTP,
    PasswordResetOTP,
    RegistrationOTP,
)

OTP_MODELS = (
    PasswordResetOTP,
    RegistrationOTP,
    AccountDeactivateOTP,
    AccountDeleteOTP,
    AccountReactivateOTP,
    ChangePasswordOTP,
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
        qs = model.objects.filter(expires_at__lt=now)
        if include_used:
            qs = qs | model.objects.filter(is_used=True)
        if dry_run:
            counts[model.__name__] = qs.distinct().count()
        else:
            deleted, _ = qs.distinct().delete()
            counts[model.__name__] = deleted

    return counts
