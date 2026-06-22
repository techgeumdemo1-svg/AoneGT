"""Post-checkout helpers: Zoho SO sync and optional async confirmation email."""

from __future__ import annotations

import logging
import threading

from django.conf import settings
from django.db import close_old_connections, transaction

logger = logging.getLogger(__name__)


def checkout_async_email_enabled() -> bool:
    return getattr(settings, 'CHECKOUT_ASYNC_EMAIL', True)


def checkout_async_zoho_enabled() -> bool:
    return getattr(settings, 'CHECKOUT_ASYNC_ZOHO_SYNC', True)


def sync_checkout_zoho_sales_order(order_id: int, *, books_manual_workflow: bool) -> None:
    """Create Zoho sales order for a placed order."""
    if books_manual_workflow:
        from shop.services.zoho_books_sales_order import maybe_create_zoho_books_sales_order_for_order

        maybe_create_zoho_books_sales_order_for_order(order_id, trigger='placed')
    else:
        from shop.services.zoho_sales_order import maybe_create_zoho_sales_order_for_order

        maybe_create_zoho_sales_order_for_order(order_id)


def _zoho_sync_worker(order_id: int, *, books_manual_workflow: bool) -> None:
    close_old_connections()
    try:
        sync_checkout_zoho_sales_order(order_id, books_manual_workflow=books_manual_workflow)
    except Exception:
        logger.exception('checkout-async: Zoho sales order sync failed order=%s', order_id)
    finally:
        close_old_connections()


def schedule_checkout_zoho_sales_order(order_id: int, *, books_manual_workflow: bool) -> bool:
    """
    Defer Zoho sales-order creation until after the checkout HTTP response.

    Returns True when sync is scheduled on a background thread.
    """
    if not checkout_async_zoho_enabled():
        return False

    def _start() -> None:
        thread = threading.Thread(
            target=_zoho_sync_worker,
            args=(order_id,),
            kwargs={'books_manual_workflow': books_manual_workflow},
            daemon=True,
            name=f'checkout-zoho-{order_id}',
        )
        thread.start()

    transaction.on_commit(_start)
    return True


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


def _books_contact_name_worker(contact_id: str, name: str, store_id: int) -> None:
    from catalog.models import Store
    from shop.services.zoho_books import books_update_contact_name

    close_old_connections()
    try:
        store = Store.objects.filter(pk=store_id).first()
        if store is None:
            return
        books_update_contact_name(contact_id, name, store=store)
    except Exception:
        logger.exception(
            'checkout-async: Books contact name update failed contact=%s store=%s',
            contact_id,
            store_id,
        )
    finally:
        close_old_connections()


def schedule_books_contact_name_update(contact_id: str, name: str, store) -> None:
    """Best-effort contact name sync — does not block checkout."""
    cid = (contact_id or '').strip()
    display_name = (name or '').strip()
    if not cid or not display_name or store is None:
        return

    def _start() -> None:
        thread = threading.Thread(
            target=_books_contact_name_worker,
            args=(cid, display_name, store.pk),
            daemon=True,
            name=f'books-contact-{cid[:8]}',
        )
        thread.start()

    transaction.on_commit(_start)


def _sales_order_hover_comment_worker(order_id: int, salesorder_id: str) -> None:
    from shop.models import Order
    from shop.services.zoho_books_sales_order import _maybe_add_sales_order_hover_comment

    close_old_connections()
    try:
        order = Order.objects.select_related('store').get(pk=order_id)
        _maybe_add_sales_order_hover_comment(order, salesorder_id)
    except Exception:
        logger.exception(
            'checkout-async: sales order hover comment failed order=%s salesorder_id=%s',
            order_id,
            salesorder_id,
        )
    finally:
        close_old_connections()


def schedule_sales_order_hover_comment(order_id: int, salesorder_id: str) -> None:
    """Best-effort SO hover comment — does not block checkout response."""
    so_id = (salesorder_id or '').strip()
    if not so_id:
        return

    def _start() -> None:
        thread = threading.Thread(
            target=_sales_order_hover_comment_worker,
            args=(order_id, so_id),
            daemon=True,
            name=f'books-so-comment-{order_id}',
        )
        thread.start()

    transaction.on_commit(_start)
