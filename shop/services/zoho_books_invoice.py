"""Create a Zoho Books invoice when an order is confirmed."""

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
    books_create_contact,
    books_create_invoice,
    books_find_contact_id_by_email,
    books_get_contact,
    store_has_books_config,
    zoho_books_enabled,
    zoho_books_vat_tax_id,
)

logger = logging.getLogger(__name__)


def _should_create_on_placed() -> bool:
    mode = (getattr(settings, 'ZOHO_BOOKS_CREATE_INVOICE_ON', 'placed') or 'placed').strip().lower()
    return mode in ('placed', 'both', 'checkout', 'confirmed')


def _should_create_on_synced() -> bool:
    mode = (getattr(settings, 'ZOHO_BOOKS_CREATE_INVOICE_ON', 'placed') or 'placed').strip().lower()
    return mode in ('synced', 'both')


def order_ready_for_books_invoice(order: Order, *, trigger: str) -> bool:
    if not zoho_books_enabled():
        return False
    store = getattr(order, 'store', None)
    if store is None and order.store_id:
        from catalog.models import Store

        store = Store.objects.filter(pk=order.store_id).first()
    if not store_has_books_config(store):
        return False
    if order.status == Order.Status.CANCELLED:
        return False
    if (order.zoho_books_invoice_id or '').strip():
        return False
    if trigger == 'placed':
        return _should_create_on_placed()
    if trigger == 'synced':
        return _should_create_on_synced()
    return False


def _decimal_str(value) -> str:
    return str(Decimal(str(value or 0)).quantize(Decimal('0.01')))


def _order_coupon_discount(order: Order) -> Decimal:
    """Offer coupon amount applied on this order (from usage log or derived totals)."""
    try:
        from offer.models import CouponUsageLog

        usage = CouponUsageLog.objects.filter(order_id=order.pk).order_by('-used_at').first()
        if usage is not None:
            return max(
                Decimal(str(usage.discount_amount_applied or 0)).quantize(Decimal('0.01')),
                Decimal('0'),
            )
    except Exception:
        pass
    taxable_subtotal = (
        Decimal(str(order.total or 0))
        - Decimal(str(order.vat_amount or 0))
        - Decimal(str(order.shipping_amount or 0))
    ).quantize(Decimal('0.01'))
    coupon = (
        Decimal(str(order.subtotal or 0))
        - Decimal(str(order.loyalty_discount or 0))
        - taxable_subtotal
    ).quantize(Decimal('0.01'))
    return max(coupon, Decimal('0'))


def _invoice_summary_notes(order: Order) -> str:
    currency = (order.currency or 'AED').strip() or 'AED'
    subtotal = Decimal(str(order.subtotal or 0)).quantize(Decimal('0.01'))
    coupon_discount = _order_coupon_discount(order)
    loyalty_discount = Decimal(str(order.loyalty_discount or 0)).quantize(Decimal('0.01'))
    taxable_subtotal = (subtotal - coupon_discount - loyalty_discount).quantize(Decimal('0.01'))
    if taxable_subtotal < 0:
        taxable_subtotal = Decimal('0.00')
    vat_percent = Decimal(str(order.vat_percent or 0)).quantize(Decimal('0.01'))
    vat_amount = Decimal(str(order.vat_amount or 0)).quantize(Decimal('0.01'))
    shipping_amount = Decimal(str(order.shipping_amount or 0)).quantize(Decimal('0.01'))
    total = Decimal(str(order.total or 0)).quantize(Decimal('0.01'))

    coupon_code = ''
    try:
        from offer.models import CouponUsageLog

        usage = CouponUsageLog.objects.filter(order_id=order.pk).order_by('-used_at').first()
        if usage is not None:
            coupon_code = (usage.coupon_code or '').strip()
    except Exception:
        pass

    lines = [
        f'AoneGt order #{order.pk}',
        '',
        'Order summary:',
        f'Subtotal\t{_decimal_str(subtotal)} {currency}',
        f'Coupon discount\t-{_decimal_str(coupon_discount)} {currency}',
        f'Loyalty discount\t-{_decimal_str(loyalty_discount)} {currency}',
        f'Taxable subtotal\t{_decimal_str(taxable_subtotal)} {currency}',
        f'VAT ({_decimal_str(vat_percent)}%)\t{_decimal_str(vat_amount)} {currency}',
        f'Shipping\t{_decimal_str(shipping_amount)} {currency}',
        f'Total\t{_decimal_str(total)} {currency}',
    ]
    if coupon_code:
        lines.insert(3, f'Coupon code\t{coupon_code}')
    return '\n'.join(lines)


def _build_invoice_payload(order: Order, customer_id: str) -> dict:
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
        'reference_number': order_code_for_order(order),
        'date': order.created_at.date().isoformat(),
        'line_items': line_items,
        'shipping_charge': float(Decimal(str(order.shipping_amount or 0))),
        'currency_code': (order.currency or 'AED').strip() or 'AED',
        'notes': _invoice_summary_notes(order),
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


def _persist_user_books_contact_id(user, contact_id: str) -> None:
    contact_id = (contact_id or '').strip()
    if not contact_id:
        return
    if (getattr(user, 'zoho_books_contact_id', '') or '').strip() == contact_id:
        return
    from accounts.models import User

    User.objects.filter(pk=user.pk).update(zoho_books_contact_id=contact_id[:64])
    user.zoho_books_contact_id = contact_id[:64]


def _resolve_customer_id(order: Order) -> str:
    user = order.user
    store = order.store
    email = (getattr(user, 'email', '') or '').strip().lower()

    stored = (getattr(user, 'zoho_books_contact_id', '') or '').strip()
    if stored and books_get_contact(stored, store=store):
        return stored
    if stored:
        from accounts.models import User

        User.objects.filter(pk=user.pk).update(zoho_books_contact_id='')
        user.zoho_books_contact_id = ''

    existing = books_find_contact_id_by_email(email, store=store) if email else None
    if existing:
        _persist_user_books_contact_id(user, existing)
        return existing

    first = (getattr(user, 'first_name', '') or '').strip()
    last = (getattr(user, 'last_name', '') or '').strip()
    name = f'{first} {last}'.strip()
    if not name:
        name = (order.shipping_name or '').strip()
    if not name:
        name = email or f'Customer {user.pk}'

    billing_address = {
        'attention': name[:100],
        'address': (order.billing_address or order.shipping_address or '')[:500],
        'city': (order.billing_city or order.shipping_city or '')[:100],
        'state': (order.billing_state or order.shipping_state or '')[:100],
        'zip': (order.billing_postal_code or order.shipping_postal_code or '')[:32],
        'country': (order.billing_country or order.shipping_country or '')[:100],
        'phone': (order.billing_phone or order.shipping_phone or '')[:50],
    }
    phone = (getattr(user, 'phone', '') or order.shipping_phone or '')[:50]
    contact_id = books_create_contact(
        contact_name=name,
        email=email,
        phone=phone,
        billing_address=billing_address,
        store=store,
    )
    _persist_user_books_contact_id(user, contact_id)
    return contact_id


def create_zoho_books_invoice_for_order(order: Order) -> bool:
    """
    Create invoice in Zoho Books and persist ids on the order.
    Returns True on success. Raises ZohoBooksError on API failure.
    """
    order = Order.objects.select_related('user', 'store').prefetch_related('items').get(pk=order.pk)
    customer_id = _resolve_customer_id(order)
    invoice_body = _build_invoice_payload(order, customer_id)
    invoice = books_create_invoice(invoice_body, store=order.store)

    invoice_id = str(invoice.get('invoice_id') or '').strip()
    invoice_number = str(invoice.get('invoice_number') or '').strip()
    if not invoice_id:
        raise ZohoBooksError('Zoho Books invoice_id missing in response.')

    order.zoho_books_invoice_id = invoice_id[:64]
    order.zoho_books_invoice_number = invoice_number[:64]
    order.zoho_books_invoice_error = ''
    order.zoho_books_invoiced_at = dj_tz.now()
    order.save(
        update_fields=[
            'zoho_books_invoice_id',
            'zoho_books_invoice_number',
            'zoho_books_invoice_error',
            'zoho_books_invoiced_at',
            'updated_at',
        ],
    )
    logger.info(
        'zoho-books: invoice created order=%s invoice_id=%s number=%s',
        order.pk,
        invoice_id,
        invoice_number,
    )
    return True


def maybe_create_zoho_books_invoice(order_id: int, *, trigger: str = 'placed') -> None:
    """
    Best-effort invoice creation; never raises to callers (checkout / admin safe).
    ``trigger`` is ``placed`` (checkout) or ``synced`` (order sync confirmed).
    """
    try:
        order = Order.objects.select_related('store').get(pk=order_id)
    except Order.DoesNotExist:
        return

    if not order_ready_for_books_invoice(order, trigger=trigger):
        return

    try:
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order_id)
            if (locked.zoho_books_invoice_id or '').strip():
                return
            create_zoho_books_invoice_for_order(locked)
    except ZohoBooksError as exc:
        logger.exception('zoho-books: invoice failed order=%s (%s)', order_id, exc)
        Order.objects.filter(pk=order_id).update(
            zoho_books_invoice_error=str(exc)[:5000],
            updated_at=dj_tz.now(),
        )
    except Exception as exc:
        logger.exception('zoho-books: unexpected error order=%s', order_id)
        Order.objects.filter(pk=order_id).update(
            zoho_books_invoice_error=str(exc)[:5000],
            updated_at=dj_tz.now(),
        )
