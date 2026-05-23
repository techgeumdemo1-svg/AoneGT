"""Create Zoho Books sales orders when an order is confirmed (or at checkout)."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone as dj_tz

from shop.models import Order
from shop.serializers import order_code_for_order
from shop.services.zoho_books import (
    ZohoBooksError,
    books_create_sales_order,
    store_has_books_config,
    zoho_books_vat_tax_id,
)
from shop.services.zoho_books_invoice import (
    _invoice_summary_notes,
    _order_coupon_discount,
    _resolve_customer_id,
)

logger = logging.getLogger(__name__)


def zoho_books_sales_order_enabled() -> bool:
    return getattr(settings, 'ZOHO_BOOKS_CREATE_SALES_ORDER_ENABLED', False)


def _should_create_on_placed() -> bool:
    mode = (
        getattr(settings, 'ZOHO_BOOKS_CREATE_SALES_ORDER_ON', 'synced') or 'synced'
    ).strip().lower()
    return mode in ('placed', 'both', 'checkout', 'confirmed')


def _should_create_on_synced() -> bool:
    mode = (
        getattr(settings, 'ZOHO_BOOKS_CREATE_SALES_ORDER_ON', 'synced') or 'synced'
    ).strip().lower()
    return mode in ('synced', 'both')


def order_ready_for_books_sales_order(order: Order, *, trigger: str) -> bool:
    if not zoho_books_sales_order_enabled():
        return False
    store = getattr(order, 'store', None)
    if store is None and order.store_id:
        from catalog.models import Store

        store = Store.objects.filter(pk=order.store_id).first()
    if not store_has_books_config(store):
        return False
    if order.status == Order.Status.CANCELLED:
        return False
    if (order.zoho_books_salesorder_id or '').strip():
        return False
    if trigger == 'placed':
        return _should_create_on_placed()
    if trigger == 'synced':
        return _should_create_on_synced()
    return False


def _build_sales_order_payload(order: Order, customer_id: str) -> dict:
    from shop.models import OrderItem

    line_items = []
    for item in OrderItem.objects.filter(order_id=order.pk):
        row = {
            'name': (item.product_name or 'Item')[:200],
            'rate': float(Decimal(str(item.unit_price))),
            'quantity': float(item.quantity),
        }
        sku = (item.sku or '').strip()
        if sku:
            row['description'] = sku[:200]
        line_items.append(row)
    if not line_items:
        line_items.append({
            'name': f'Order #{order.pk}',
            'rate': float(Decimal(str(order.total))),
            'quantity': 1.0,
        })

    payload: dict = {
        'customer_id': customer_id,
        'reference_number': order_code_for_order(order)[:100],
        'date': order.created_at.date().isoformat(),
        'line_items': line_items,
        'shipping_charge': float(Decimal(str(order.shipping_amount or 0))),
        'currency_code': (order.currency or 'AED').strip() or 'AED',
        'notes': _invoice_summary_notes(order)[:500],
    }

    coupon_discount = _order_coupon_discount(order)
    loyalty_discount = Decimal(str(order.loyalty_discount or 0))
    total_discount = (coupon_discount + loyalty_discount).quantize(Decimal('0.01'))
    if total_discount > 0:
        payload['discount'] = float(total_discount)
        payload['discount_type'] = 'entity_level'
        payload['is_discount_before_tax'] = True

    tax_id = zoho_books_vat_tax_id(store=order.store)
    if tax_id:
        for row in payload['line_items']:
            row['tax_id'] = tax_id

    return payload


def create_zoho_books_sales_order_for_order(order: Order) -> bool:
    """Create sales order in Zoho Books. Returns True on success. Raises ZohoBooksError."""
    order = Order.objects.select_related('user', 'store').prefetch_related('items').get(pk=order.pk)
    customer_id = _resolve_customer_id(order)
    salesorder_body = _build_sales_order_payload(order, customer_id)
    salesorder = books_create_sales_order(salesorder_body, store=order.store)

    salesorder_id = str(salesorder.get('salesorder_id') or '').strip()
    salesorder_number = str(salesorder.get('salesorder_number') or '').strip()
    if not salesorder_id:
        raise ZohoBooksError('Zoho Books salesorder_id missing in response.')

    order.zoho_books_salesorder_id = salesorder_id[:64]
    order.zoho_books_salesorder_number = salesorder_number[:64]
    order.zoho_books_salesorder_error = ''
    order.zoho_books_salesordered_at = dj_tz.now()
    order.save(
        update_fields=[
            'zoho_books_salesorder_id',
            'zoho_books_salesorder_number',
            'zoho_books_salesorder_error',
            'zoho_books_salesordered_at',
            'updated_at',
        ],
    )
    logger.info(
        'zoho-books: sales order created order=%s salesorder_id=%s number=%s',
        order.pk,
        salesorder_id,
        salesorder_number,
    )
    return True


def maybe_create_zoho_books_sales_order_for_order(order_id: int, *, trigger: str = 'synced') -> None:
    """Best-effort Books sales order creation; never raises to callers."""
    try:
        order = Order.objects.select_related('store').get(pk=order_id)
    except Order.DoesNotExist:
        return

    if not order_ready_for_books_sales_order(order, trigger=trigger):
        return

    try:
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order_id)
            if (locked.zoho_books_salesorder_id or '').strip():
                return
            create_zoho_books_sales_order_for_order(locked)
    except ZohoBooksError as exc:
        logger.exception('zoho-books: sales order failed order=%s (%s)', order_id, exc)
        Order.objects.filter(pk=order_id).update(
            zoho_books_salesorder_error=str(exc)[:5000],
            updated_at=dj_tz.now(),
        )
    except Exception as exc:
        logger.exception('zoho-books: unexpected sales order error order=%s', order_id)
        Order.objects.filter(pk=order_id).update(
            zoho_books_salesorder_error=str(exc)[:5000],
            updated_at=dj_tz.now(),
        )
