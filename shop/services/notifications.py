"""Create in-app notifications for the authenticated shop user feed."""

from __future__ import annotations

from typing import Any

from shop.models import UserNotification


def create_user_notification(
    user,
    kind: str,
    *,
    title: str,
    body: str = '',
    payload: dict[str, Any] | None = None,
) -> None:
    UserNotification.objects.create(
        user=user,
        kind=kind,
        title=(title or '')[:255],
        body=body or '',
        payload=payload or {},
    )
