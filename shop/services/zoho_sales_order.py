"""Create and update Zoho Commerce sales orders from local shop orders."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings
from django.utils import timezone as dj_tz

from shop.models import Order, OrderItem
from shop.serializers import order_code_for_order
from shop.services.zoho_commerce import ZohoCommerceError, ZohoCommerceService

logger = logging.getLogger(__name__)


class ZohoSalesOrderError(Exception):
    """Zoho Commerce sales order API failure."""


def zoho_commerce_sales_order_enabled() -> bool:
    return getattr(settings, 'ZOHO_COMMERCE_CREATE_SALES_ORDER_ENABLED', False)


COMMERCE_PAYMENT_MODE_BY_METHOD = {
    Order.PaymentMethod.CASH_ON_DELIVERY: 'Cash On Delivery',
    Order.PaymentMethod.CARD_ON_DELIVERY: 'Card On Delivery',
    Order.PaymentMethod.PAYMENT_GATEWAY: 'Razorpay',
    Order.PaymentMethod.PAY_BY_LINK: 'Bank Transfer',
}


def _commerce_payment_mode(order: Order) -> str:
    return COMMERCE_PAYMENT_MODE_BY_METHOD.get(
        Order.PaymentMethod(order.payment_method),
        order.get_payment_method_display(),
    )


def _zoho_address(
    *,
    street: str,
    city: str,
    state: str,
    zip_code: str,
    country: str,
) -> dict[str, str]:
    # Zoho Commerce address fields are capped at ~100 chars per value.
    return {
        'street': (street or '')[:100],
        'city': (city or '')[:100],
        'state': (state or '')[:100],
        'zip': (zip_code or '')[:32],
        'country': (country or '')[:100],
    }


def _order_discount_total(order: Order) -> Decimal:
    from shop.services.zoho_books_invoice import _order_coupon_discount

    loyalty = Decimal(str(order.loyalty_discount or 0)).quantize(Decimal('0.01'))
    coupon = _order_coupon_discount(order)
    return (loyalty + coupon).quantize(Decimal('0.01'))


def _build_line_items(order: Order) -> list[dict]:
    rows: list[dict] = []
    for item in order.items.select_related('product').all():
        zoho_item_id = ''
        if item.product_id and item.product:
            zoho_item_id = (item.product.zoho_product_id or '').strip()
        if not zoho_item_id:
            raise ZohoSalesOrderError(
                f'Product "{item.product_name}" is missing zoho_product_id (order item {item.pk}).',
            )
        unit_price = Decimal(str(item.unit_price or 0)).quantize(Decimal('0.01'))
        line_total = Decimal(str(item.line_total or 0)).quantize(Decimal('0.01'))
        rows.append({
            'item_id': zoho_item_id,
            'name': (item.product_name or '')[:100],
            'rate': float(unit_price),
            'quantity': int(item.quantity or 0),
            'item_total': float(line_total),
        })
    if not rows:
        raise ZohoSalesOrderError('Order has no line items for Zoho sales order.')
    return rows


def _build_sales_order_body(order: Order) -> dict:
    """
    Flat JSON body for POST/PUT /store/api/v1/salesorders.

    Do not wrap in ``{"salesorder": ...}`` — Zoho Commerce rejects that with a
    misleading 100-character validation error.
    """
    user = order.user
    email = (getattr(user, 'email', '') or '').strip()

    discount_total = _order_discount_total(order)
    body: dict = {
        'reference_number': order_code_for_order(order)[:100],
        'date': order.created_at.date().isoformat(),
        'currency_code': (order.currency or 'AED').strip() or 'AED',
        'payment_mode': _commerce_payment_mode(order)[:100],
        'is_offline_payment': order.payment_method in (
            Order.PaymentMethod.CASH_ON_DELIVERY.value,
            Order.PaymentMethod.CARD_ON_DELIVERY.value,
        ),
        'customer_name': (order.shipping_name or email or f'Customer {user.pk}')[:100],
        'billing_address': _zoho_address(
            street=order.billing_address or order.shipping_address,
            city=order.billing_city or order.shipping_city,
            state=order.billing_state or order.shipping_state,
            zip_code=order.billing_postal_code or order.shipping_postal_code,
            country=order.billing_country or order.shipping_country,
        ),
        'shipping_address': _zoho_address(
            street=order.shipping_address,
            city=order.shipping_city,
            state=order.shipping_state,
            zip_code=order.shipping_postal_code,
            country=order.shipping_country,
        ),
        'line_items': _build_line_items(order),
        'shipping_charge': float(Decimal(str(order.shipping_amount or 0)).quantize(Decimal('0.01'))),
        'notes': f'AoneGt order #{order.pk}'[:100],
    }
    if email:
        body['customer_email'] = email[:100]
    if discount_total > 0:
        body['discount'] = float(discount_total)
        body['discount_type'] = 'entity_level'
        body['is_discount_before_tax'] = True
    return body


def _persist_line_item_ids(order: Order, sales_order: dict) -> None:
    zoho_lines = sales_order.get('line_items') or []
    if not isinstance(zoho_lines, list):
        return

    local_items = list(order.items.select_related('product').order_by('id'))
    used_local: set[int] = set()

    for zline in zoho_lines:
        if not isinstance(zline, dict):
            continue
        zoho_line_id = str(zline.get('line_item_id') or zline.get('salesorder_item_id') or '').strip()
        zoho_item_id = str(zline.get('item_id') or '').strip()
        if not zoho_line_id:
            continue

        matched = None
        for local in local_items:
            if local.pk in used_local:
                continue
            local_zoho_id = ''
            if local.product_id and local.product:
                local_zoho_id = (local.product.zoho_product_id or '').strip()
            if zoho_item_id and local_zoho_id == zoho_item_id:
                matched = local
                break
        if matched is None:
            for local in local_items:
                if local.pk not in used_local:
                    matched = local
                    break
        if matched is None:
            continue
        used_local.add(matched.pk)
        if matched.zoho_line_item_id != zoho_line_id:
            OrderItem.objects.filter(pk=matched.pk).update(zoho_line_item_id=zoho_line_id[:120])


def _store_ready(order: Order) -> bool:
    store = order.store
    org = (getattr(store, 'zoho_org_id', '') or '').strip()
    return bool(org)


def create_zoho_sales_order_for_order(order: Order) -> str:
    """Create sales order in Zoho Commerce. Returns salesorder_id. Raises ZohoSalesOrderError."""
    order = Order.objects.select_related('store', 'user').prefetch_related('items__product').get(pk=order.pk)
    if not _store_ready(order):
        raise ZohoSalesOrderError('Store is missing zoho_org_id for Commerce sales orders.')

    body = _build_sales_order_body(order)
    try:
        response = ZohoCommerceService.admin_post('salesorders', body, store=order.store)
    except ZohoCommerceError as exc:
        raise ZohoSalesOrderError(str(exc)) from exc

    if not isinstance(response, dict):
        raise ZohoSalesOrderError('Unexpected Zoho Commerce response type.')

    code = response.get('code')
    if code not in (0, '0', None):
        raise ZohoSalesOrderError(str(response.get('message') or response))

    sales_order = response.get('salesorder')
    if not isinstance(sales_order, dict):
        raise ZohoSalesOrderError('Zoho Commerce did not return salesorder payload.')

    sales_order_id = str(sales_order.get('salesorder_id') or '').strip()
    if not sales_order_id:
        raise ZohoSalesOrderError('Zoho Commerce salesorder_id missing in response.')

    _persist_line_item_ids(order, sales_order)
    return sales_order_id


def update_zoho_sales_order_for_order(order: Order) -> None:
    """Update existing Zoho Commerce sales order. Raises ZohoSalesOrderError."""
    order = Order.objects.select_related('store', 'user').prefetch_related('items__product').get(pk=order.pk)
    sales_order_id = (order.zoho_salesorder_id or '').strip()
    if not sales_order_id:
        raise ZohoSalesOrderError('Order has no zoho_salesorder_id to update.')

    if not _store_ready(order):
        raise ZohoSalesOrderError('Store is missing zoho_org_id for Commerce sales orders.')

    body = _build_sales_order_body(order)
    resource = f'salesorders/{sales_order_id}'
    try:
        response = ZohoCommerceService.admin_put(resource, body, store=order.store)
    except ZohoCommerceError as exc:
        raise ZohoSalesOrderError(str(exc)) from exc

    if isinstance(response, dict):
        code = response.get('code')
        if code not in (0, '0', None):
            raise ZohoSalesOrderError(str(response.get('message') or response))
        sales_order = response.get('salesorder')
        if isinstance(sales_order, dict):
            _persist_line_item_ids(order, sales_order)


def _record_sync_error(order_id: int, message: str) -> None:
    Order.objects.filter(pk=order_id).update(
        zoho_sync_error=str(message)[:5000],
        updated_at=dj_tz.now(),
    )


def maybe_create_zoho_sales_order_for_order(order_id: int) -> None:
    """Best-effort create after checkout. Never raises."""
    if not zoho_commerce_sales_order_enabled():
        return

    try:
        order = Order.objects.select_related('store', 'user').prefetch_related('items__product').get(pk=order_id)
    except Order.DoesNotExist:
        return

    if (order.zoho_salesorder_id or '').strip():
        return

    try:
        sales_order_id = create_zoho_sales_order_for_order(order)
    except ZohoSalesOrderError as exc:
        logger.exception('zoho-commerce: sales order create failed order=%s', order_id)
        _record_sync_error(order_id, exc)
        return
    except Exception as exc:
        logger.exception('zoho-commerce: unexpected sales order error order=%s', order_id)
        _record_sync_error(order_id, exc)
        return

    Order.objects.filter(pk=order_id).update(
        zoho_salesorder_id=sales_order_id[:120],
        zoho_sync_error='',
        updated_at=dj_tz.now(),
    )
    logger.info('zoho-commerce: sales order created order=%s salesorder_id=%s', order_id, sales_order_id)


def maybe_update_zoho_sales_order_for_order(order_id: int) -> None:
    """Best-effort update after local order edit. Never raises."""
    if not zoho_commerce_sales_order_enabled():
        return

    try:
        order = Order.objects.select_related('store').get(pk=order_id)
    except Order.DoesNotExist:
        return

    if not (order.zoho_salesorder_id or '').strip():
        maybe_create_zoho_sales_order_for_order(order_id)
        return

    try:
        update_zoho_sales_order_for_order(order)
    except ZohoSalesOrderError as exc:
        logger.exception('zoho-commerce: sales order update failed order=%s', order_id)
        _record_sync_error(order_id, exc)
    except Exception as exc:
        logger.exception('zoho-commerce: unexpected sales order update error order=%s', order_id)
        _record_sync_error(order_id, exc)
    else:
        Order.objects.filter(pk=order_id).update(zoho_sync_error='', updated_at=dj_tz.now())
