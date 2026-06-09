"""Persist and read per-stage timestamps for customer order tracking."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from django.utils import timezone

from shop.models import Order, OrderReturn

TRACKING_HISTORY_CANCELLED_KEY = 'cancelled'


def get_tracking_history(order: Order) -> dict:
    raw = getattr(order, 'tracking_stage_history', None) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _isoformat(dt: datetime) -> str:
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt.isoformat()


def record_tracking_stage(
    order: Order,
    stage: str,
    *,
    at: Optional[datetime] = None,
    save: bool = False,
) -> None:
    """Record when a tracking stage was reached (overwrites prior time for that stage)."""
    key = (stage or '').strip().lower()
    if not key:
        return

    when = at or timezone.now()
    history = get_tracking_history(order)
    history[key] = _isoformat(when)
    order.tracking_stage_history = history

    if save:
        order.save(update_fields=['tracking_stage_history', 'updated_at'])


def ensure_pending_recorded(order: Order, *, save: bool = False) -> None:
    history = get_tracking_history(order)
    if 'pending' in history:
        return
    when = order.created_at or timezone.now()
    record_tracking_stage(order, 'pending', at=when, save=save)


def tracking_stage_at(order: Order, stage_key: str) -> Optional[str]:
    key = (stage_key or '').strip().lower()
    if not key:
        return None

    history = get_tracking_history(order)
    if key in history:
        return history[key]

    if key == 'pending' and order.created_at:
        return _isoformat(order.created_at)

    if key == 'returned':
        return _inferred_returned_at(order)

    return None


def cancelled_at(order: Order) -> Optional[str]:
    history = get_tracking_history(order)
    if TRACKING_HISTORY_CANCELLED_KEY in history:
        return history[TRACKING_HISTORY_CANCELLED_KEY]
    if order.status == Order.Status.CANCELLED and order.updated_at:
        return _isoformat(order.updated_at)
    return None


def _inferred_returned_at(order: Order) -> Optional[str]:
    ret = (
        order.returns.filter(status=OrderReturn.Status.COMPLETED)
        .order_by('-updated_at')
        .first()
    )
    if ret and ret.updated_at:
        return _isoformat(ret.updated_at)
    return None


def tracking_stage_events(order: Order) -> list:
    """Ordered list of recorded stage timestamps for admin timeline."""
    ensure_pending_recorded(order)
    events = []
    for stage_key, label in (
        ('pending', 'Pending'),
        ('packed', 'Packed'),
        ('out_for_delivery', 'Out for Delivery'),
        ('delivered', 'Delivered'),
        ('returned', 'Returned'),
    ):
        at = tracking_stage_at(order, stage_key)
        if at:
            events.append({'key': stage_key, 'label': label, 'at': at})

    cancelled = cancelled_at(order)
    if cancelled:
        events.append({'key': 'cancelled', 'label': 'Cancelled', 'at': cancelled})
    return events
