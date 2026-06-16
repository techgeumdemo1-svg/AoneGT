"""Remove in-app notifications older than the configured retention period."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from shop.models import UserNotification

logger = logging.getLogger(__name__)


def purge_old_notifications(
    *,
    retention_days: int | None = None,
    user=None,
    dry_run: bool = False,
) -> int:
    """
    Delete UserNotification rows with created_at older than retention_days.

    Returns the number of rows that would be or were deleted.
    Set retention_days to 0 (or NOTIFICATION_RETENTION_DAYS=0) to disable purging.
    """
    days = (
        retention_days
        if retention_days is not None
        else int(getattr(settings, 'NOTIFICATION_RETENTION_DAYS', 30) or 0)
    )
    if days <= 0:
        return 0

    cutoff = timezone.now() - timedelta(days=days)
    qs = UserNotification.objects.filter(created_at__lt=cutoff)
    if user is not None:
        qs = qs.filter(user=user)

    if dry_run:
        return qs.count()

    deleted, breakdown = qs.delete()
    if deleted:
        logger.info(
            'notification-purge: deleted %s row(s) older than %s days user=%s breakdown=%s',
            deleted,
            days,
            getattr(user, 'pk', None) if user is not None else 'all',
            breakdown,
        )
    return deleted
