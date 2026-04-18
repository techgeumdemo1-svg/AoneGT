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
