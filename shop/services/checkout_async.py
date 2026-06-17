"""Post-checkout helpers: Zoho SO sync (inline) and optional async confirmation email."""

from __future__ import annotations

import logging
import threading

from django.conf import settings
from django.db import close_old_connections, transaction

logger = logging.getLogger(__name__)


def checkout_async_email_enabled() -> bool:
    return getattr(settings, 'CHECKOUT_ASYNC_EMAIL', True)


def sync_checkout_zoho_sales_order(order_id: int, *, books_manual_workflow: bool) -> None:
    """Create Zoho sales order before checkout response (so SO id is in the payload)."""
    if books_manual_workflow:
        from shop.services.zoho_books_sales_order import maybe_create_zoho_books_sales_order_for_order

        maybe_create_zoho_books_sales_order_for_order(order_id, trigger='placed')
    else:
        from shop.services.zoho_sales_order import maybe_create_zoho_sales_order_for_order

        maybe_create_zoho_sales_order_for_order(order_id)


def _send_confirmation_email(order_id: int, user_id: int) -> None:
    from django.contrib.auth import get_user_model

    from shop.models import Order
    from shop.services.order_email import send_order_placed_email

    order = Order.objects.prefetch_related('items').get(pk=order_id)
    user = get_user_model().objects.get(pk=user_id)
    send_order_placed_email(order, user)


def _email_worker(order_id: int, user_id: int) -> None:
    close_old_connections()
    try:
        _send_confirmation_email(order_id, user_id)
    except Exception:
        logger.exception('checkout-async: confirmation email failed order=%s', order_id)
    finally:
        close_old_connections()


def schedule_confirmation_email(order_id: int, user_id: int) -> bool:
    """
    Send order confirmation email after checkout.

    Returns True when email is deferred to a background thread (caller should
    not call send_order_placed_email synchronously).
    """
    if not checkout_async_email_enabled():
        return False

    def _start() -> None:
        thread = threading.Thread(
            target=_email_worker,
            args=(order_id, user_id),
            daemon=True,
            name=f'checkout-email-{order_id}',
        )
        thread.start()

    transaction.on_commit(_start)
    return True
