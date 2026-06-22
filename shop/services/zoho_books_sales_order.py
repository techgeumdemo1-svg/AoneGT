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
    books_add_sales_order_comment,
    books_create_sales_order,
    books_update_sales_order,
    books_void_sales_order,
    store_has_books_config,
    zoho_books_vat_tax_id,
)
from shop.services.zoho_books_invoice import (
    _invoice_summary_notes,
    _order_coupon_discount,
    _resolve_customer_id,
)
from shop.services.zoho_returns import persist_books_sales_order_line_item_ids

logger = logging.getLogger(__name__)


def zoho_books_sales_order_enabled() -> bool:
    return getattr(settings, 'ZOHO_BOOKS_CREATE_SALES_ORDER_ENABLED', False)


def _should_create_on_placed() -> bool:
    mode = (
        getattr(settings, 'ZOHO_BOOKS_CREATE_SALES_ORDER_ON', 'synced') or 'synced'
    ).strip().lower()
    return mode in ('placed', 'both', 'checkout', 'confirmed')


def _should_create_on_synced() -> bool:
    from shop.services.zoho_books_invoice import zoho_books_manual_workflow

    if zoho_books_manual_workflow():
        return False
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
        from shop.services.zoho_books_invoice import zoho_books_manual_workflow

        if zoho_books_manual_workflow():
            return True
        return _should_create_on_placed()
    if trigger == 'synced':
        return _should_create_on_synced()
    return False


def _payment_method_label_for_zoho(order: Order) -> str:
    try:
        return Order.PaymentMethod(order.payment_method).label
    except (ValueError, TypeError):
        return (order.payment_method or '').replace('_', ' ').title()


def _sales_order_hover_text(order: Order) -> str:
    """Compact two-line summary for Sales Order list hover / custom field."""
    return (
        f'AoneGt order #{order.pk}\n'
        f'Payment method - {_payment_method_label_for_zoho(order)}'
    )


def _books_sales_order_custom_fields(order: Order) -> list[dict]:
    """Custom fields surfaced on the Zoho Books Sales Order list."""
    fields: list[dict] = []

    payment_api = (
        getattr(settings, 'ZOHO_BOOKS_SO_PAYMENT_METHOD_CF_API_NAME', '') or ''
    ).strip()
    if payment_api:
        label = _payment_method_label_for_zoho(order)
        if label:
            fields.append({'api_name': payment_api, 'value': label[:255]})

    hover_api = (getattr(settings, 'ZOHO_BOOKS_SO_HOVER_CF_API_NAME', '') or '').strip()
    if hover_api:
        fields.append({'api_name': hover_api, 'value': _sales_order_hover_text(order)[:255]})

    return fields


def _maybe_add_sales_order_hover_comment(order: Order, salesorder_id: str) -> None:
    """Best-effort list/history note; does not fail sales order creation."""
    try:
        books_add_sales_order_comment(
            salesorder_id,
            _sales_order_hover_text(order),
            store=order.store,
        )
    except Exception as exc:
        logger.warning(
            'zoho-books: sales order hover comment failed order=%s salesorder_id=%s (%s)',
            order.pk,
            salesorder_id,
            exc,
        )


def _books_unknown_custom_field_error(exc: ZohoBooksError) -> bool:
    msg = str(exc).lower()
    return 'custom field' in msg and ('doesnot exist' in msg or 'does not exist' in msg)


def _books_create_sales_order_with_custom_field_fallback(
    salesorder_body: dict,
    *,
    store,
) -> dict:
    try:
        return books_create_sales_order(salesorder_body, store=store)
    except ZohoBooksError as exc:
        if salesorder_body.get('custom_fields') and _books_unknown_custom_field_error(exc):
            logger.warning(
                'zoho-books: payment-method custom field rejected; retrying without custom_fields (%s)',
                exc,
            )
            retry_body = {key: value for key, value in salesorder_body.items() if key != 'custom_fields'}
            return books_create_sales_order(retry_body, store=store)
        raise


def _books_update_sales_order_with_custom_field_fallback(
    salesorder_id: str,
    salesorder_body: dict,
    *,
    store,
) -> dict:
    try:
        return books_update_sales_order(salesorder_id, salesorder_body, store=store)
    except ZohoBooksError as exc:
        if salesorder_body.get('custom_fields') and _books_unknown_custom_field_error(exc):
            logger.warning(
                'zoho-books: payment-method custom field rejected on update; retrying without custom_fields (%s)',
                exc,
            )
            retry_body = {key: value for key, value in salesorder_body.items() if key != 'custom_fields'}
            return books_update_sales_order(salesorder_id, retry_body, store=store)
        raise


def _build_sales_order_payload(order: Order, customer_id: str) -> dict:
    from shop.models import OrderItem

    # FIXED: Retrieve coupon/loyalty discounts up-front so we can distribute them
    # at the line-item level instead of using a top-level transaction discount.
    coupon_discount = _order_coupon_discount(order)
    loyalty_discount = Decimal(str(order.loyalty_discount or 0))
    total_discount = (coupon_discount + loyalty_discount).quantize(Decimal('0.01'))

    # FIXED: Detect coupon type from CouponUsageLog.
    is_free_shipping_coupon = False
    is_bxgy_coupon = False  # FIXED: detect buyxgety
    try:
        from offer.models import CouponUsageLog
        usage = CouponUsageLog.objects.filter(order_id=order.pk).order_by('-used_at').first()
        if usage is not None:
            ctype = (usage.coupon_type or '').lower()
            if ctype == 'free_shipping':
                is_free_shipping_coupon = True
            elif ctype == 'buyxgety':
                is_bxgy_coupon = True  # FIXED: flag bxgy orders
    except Exception:
        pass

    # Build product line items first (no shipping yet).
    product_line_items = []
    for item in OrderItem.objects.filter(order_id=order.pk):
        row = {
            'name': (item.product_name or 'Item')[:200],
            'rate': float(Decimal(str(item.unit_price))),
            'quantity': float(item.quantity),
        }
        sku = (item.sku or '').strip()
        if sku:
            row['description'] = sku[:200]

        if is_bxgy_coupon:
            # FIXED: for buyxgety, derive per-item discount from stored line_total.
            # The get-item has line_total = unit_price * qty - discount (net price).
            # The buy-item has line_total = unit_price * qty (no discount on buy-item).
            gross = (Decimal(str(item.unit_price)) * item.quantity).quantize(Decimal('0.01'))
            stored_line = Decimal(str(item.line_total)).quantize(Decimal('0.01'))
            item_discount = max(gross - stored_line, Decimal('0')).quantize(Decimal('0.01'))
            if item_discount > Decimal('0'):
                row['discount'] = float(item_discount)       # FIXED: only on get-item
                row['discount_type'] = 'item_level'          # FIXED: item-level
        product_line_items.append(row)

    if not product_line_items:
        product_line_items.append({
            'name': f'Order #{order.pk}',
            'rate': float(Decimal(str(order.total))),
            'quantity': 1.0,
        })

    # Distribute product-level discounts (transaction / item / loyalty)
    # equally across non-shipping product line items at item level.
    # For free_shipping coupons: coupon discount goes to shipping line only,
    # but loyalty_discount still applies equally to product line items.
    # For bxgy coupons: bxgy get-item discount already set per-item above;
    # loyalty_discount splits equally across cart items only (not the get-item).
    # For item-level coupons, only eligible items get the coupon discount.
    # Shipping never gets product discounts.
    if loyalty_discount > Decimal('0') and is_bxgy_coupon:
        # Identify which product_line_items are cart items vs the bxgy get-item.
        # The get-item already has a discount set (from stored line_total diff).
        # Cart items have discount == 0 (or not set). Split loyalty only across cart items.
        from shop.models import OrderItem as OI
        oi_list = list(OI.objects.filter(order_id=order.pk))
        # Identify get-item indices: those where stored line_total < gross (have a bxgy discount)
        cart_item_indices = []
        for idx, oi in enumerate(oi_list):
            if idx >= len(product_line_items):
                break
            gross = (Decimal(str(oi.unit_price)) * oi.quantity).quantize(Decimal('0.01'))
            stored = Decimal(str(oi.line_total)).quantize(Decimal('0.01'))
            is_get_item = (gross - stored) > Decimal('0')
            if not is_get_item:
                cart_item_indices.append(idx)
        n_cart = len(cart_item_indices)
        if n_cart > 0:
            loyalty_per_cart_item = (loyalty_discount / Decimal(str(n_cart))).quantize(Decimal('0.01'))
            loyalty_remainder = loyalty_discount - (loyalty_per_cart_item * n_cart)
            for i, idx in enumerate(cart_item_indices):
                loy = loyalty_per_cart_item + (loyalty_remainder if i == 0 else Decimal('0'))
                existing = Decimal(str(product_line_items[idx].get('discount') or '0'))
                product_line_items[idx]['discount'] = float(existing + loy)
                product_line_items[idx]['discount_type'] = 'item_level'
    elif loyalty_discount > Decimal('0') and is_free_shipping_coupon:
        # Free shipping + loyalty: distribute only loyalty_discount across product items
        n_product_items = len(product_line_items)
        if n_product_items > 0:
            per_item_discount = (loyalty_discount / Decimal(str(n_product_items))).quantize(Decimal('0.01'))
            remainder = loyalty_discount - (per_item_discount * n_product_items)
            for idx, row in enumerate(product_line_items):
                item_discount = per_item_discount + (remainder if idx == 0 else Decimal('0'))
                if item_discount > Decimal('0'):
                    row['discount'] = float(item_discount)
                    row['discount_type'] = 'item_level'
    elif total_discount > Decimal('0') and not is_free_shipping_coupon and not is_bxgy_coupon:
        # FIXED: detect item-level coupon so we can target only eligible product items.
        is_item_coupon = False
        item_coupon_eligible_zoho_ids: set[str] = set()
        item_coupon_eligible_category_ids: set[str] = set()
        item_coupon_eligible_collection_ids: set[str] = set()
        try:
            from offer.models import CouponUsageLog, Coupon as CouponModel
            usage = CouponUsageLog.objects.filter(order_id=order.pk).order_by('-used_at').first()
            if usage is not None and (usage.coupon_type or '').lower() == 'item' and usage.coupon_id:
                coupon_obj = CouponModel.objects.filter(pk=usage.coupon_id).first()
                if coupon_obj is not None:
                    from offer.services import _json_dict, _json_list
                    eligible = _json_dict(coupon_obj.eligible_products)
                    pid_list = _json_list(eligible.get('products'))
                    cat_list = _json_list(eligible.get('categories'))
                    col_list = _json_list(eligible.get('collections'))
                    # Extract raw string IDs from whatever structure Zoho returns
                    def _extract_id(x):
                        if isinstance(x, dict):
                            return str(x.get('product_id') or x.get('id') or x.get('category_id') or x.get('collection_id') or '').strip()
                        return str(x).strip()
                    item_coupon_eligible_zoho_ids = {_extract_id(x) for x in pid_list if _extract_id(x)}
                    item_coupon_eligible_category_ids = {_extract_id(x) for x in cat_list if _extract_id(x)}
                    item_coupon_eligible_collection_ids = {_extract_id(x) for x in col_list if _extract_id(x)}
                    # If no restrictions defined → coupon applies to all products
                    is_item_coupon = True
        except Exception:
            pass  # fall back to equal distribution below

        if is_item_coupon and (item_coupon_eligible_zoho_ids or item_coupon_eligible_category_ids or item_coupon_eligible_collection_ids):
            # item coupon targets only eligible items; loyalty splits across ALL items.
            # Compute them separately and combine per item.
            from shop.models import OrderItem as OI
            oi_list = list(OI.objects.filter(order_id=order.pk).select_related('product'))
            n_all = len(oi_list)

            # Split loyalty equally across all product items
            loyalty_per_item = (
                (loyalty_discount / Decimal(str(n_all))).quantize(Decimal('0.01'))
                if n_all > 0 else Decimal('0')
            )
            loyalty_remainder = loyalty_discount - (loyalty_per_item * n_all)

            # Identify eligible item indices for the coupon discount
            eligible_indices = []
            for idx, oi in enumerate(oi_list):
                if idx >= len(product_line_items):
                    break
                prod = oi.product
                if prod is None:
                    continue
                zoho_pid = (getattr(prod, 'zoho_product_id', '') or '').strip()
                zoho_cat = (getattr(prod, 'zoho_category_id', '') or '').strip()
                zoho_col = (getattr(prod, 'zoho_collection_id', '') or '').strip()
                matched = (
                    (zoho_pid and zoho_pid in item_coupon_eligible_zoho_ids)
                    or (zoho_cat and zoho_cat in item_coupon_eligible_category_ids)
                    or (zoho_col and zoho_col in item_coupon_eligible_collection_ids)
                )
                if matched:
                    eligible_indices.append(idx)

            n_eligible = len(eligible_indices)
            coupon_per_eligible = (
                (coupon_discount / Decimal(str(n_eligible))).quantize(Decimal('0.01'))
                if n_eligible > 0 else Decimal('0')
            )
            coupon_remainder = coupon_discount - (coupon_per_eligible * n_eligible)

            for idx in range(len(product_line_items)):
                loy = loyalty_per_item + (loyalty_remainder if idx == 0 else Decimal('0'))
                coup = Decimal('0')
                if idx in eligible_indices:
                    ei = eligible_indices.index(idx)
                    coup = coupon_per_eligible + (coupon_remainder if ei == 0 else Decimal('0'))
                item_discount = loy + coup
                if item_discount > Decimal('0'):
                    product_line_items[idx]['discount'] = float(item_discount)
                    product_line_items[idx]['discount_type'] = 'item_level'
        else:
            # transaction / loyalty: equal split across all product items.
            n_product_items = len(product_line_items)
            if n_product_items > 0:
                per_item_discount = (total_discount / Decimal(str(n_product_items))).quantize(Decimal('0.01'))
                remainder = total_discount - (per_item_discount * n_product_items)
                for idx, row in enumerate(product_line_items):
                    item_discount = per_item_discount + (remainder if idx == 0 else Decimal('0'))
                    if item_discount > Decimal('0'):
                        row['discount'] = float(item_discount)
                        row['discount_type'] = 'item_level'

    # Zoho Books rejects the top-level 'shipping_charge' field for VAT-registered orgs.
    # Always add shipping as a dedicated line item.
    line_items = list(product_line_items)
    shipping_amount = Decimal(str(order.shipping_amount or 0))
    if shipping_amount > 0:
        shipping_row: dict = {
            'name': 'Shipping',
            'rate': float(shipping_amount),
            'quantity': 1.0,
        }
        if is_free_shipping_coupon:
            # FIXED: free_shipping — shipping line gets full discount, tax stays Exempt.
            shipping_row['discount'] = float(shipping_amount)
            shipping_row['discount_type'] = 'item_level'
        line_items.append(shipping_row)

    payload: dict = {
        'customer_id': customer_id,
        'reference_number': order_code_for_order(order)[:100],
        'date': order.created_at.date().isoformat(),
        'line_items': line_items,
        'currency_code': (order.currency or 'AED').strip() or 'AED',
        'notes': _invoice_summary_notes(order)[:500],
        # FIXED: item_level so Zoho Books reads per-line discounts only.
        'discount_type': 'item_level',
    }
    # FIXED: NO top-level 'discount' key.

    tax_id = zoho_books_vat_tax_id(store=order.store)
    if tax_id:
        # Apply VAT tax to product line items only; shipping is VAT-exempt.
        for row in payload['line_items']:
            if row.get('name') != 'Shipping':
                row['tax_id'] = tax_id

    custom_fields = _books_sales_order_custom_fields(order)
    if custom_fields:
        payload['custom_fields'] = custom_fields

    return payload


def create_zoho_books_sales_order_for_order(order: Order) -> bool:
    """Create sales order in Zoho Books. Returns True on success. Raises ZohoBooksError."""
    order = Order.objects.select_related('user', 'store').prefetch_related('items').get(pk=order.pk)
    customer_id = _resolve_customer_id(order)
    salesorder_body = _build_sales_order_payload(order, customer_id)
    salesorder = _books_create_sales_order_with_custom_field_fallback(
        salesorder_body,
        store=order.store,
    )

    salesorder_id = str(salesorder.get('salesorder_id') or '').strip()
    salesorder_number = str(salesorder.get('salesorder_number') or '').strip()
    if not salesorder_id:
        raise ZohoBooksError('Zoho Books salesorder_id missing in response.')

    persist_books_sales_order_line_item_ids(order, salesorder)

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
    from shop.services.checkout_async import schedule_sales_order_hover_comment

    schedule_sales_order_hover_comment(order.pk, salesorder_id)
    return True


def update_zoho_books_sales_order_for_order(order: Order) -> bool:
    """Update an existing Zoho Books sales order from local order data."""
    order = Order.objects.select_related('user', 'store').prefetch_related('items').get(pk=order.pk)
    salesorder_id = (order.zoho_books_salesorder_id or '').strip()
    if not salesorder_id:
        raise ZohoBooksError('Order has no Zoho Books sales order to update.')

    customer_id = _resolve_customer_id(order)
    salesorder_body = _build_sales_order_payload(order, customer_id)
    salesorder = _books_update_sales_order_with_custom_field_fallback(
        salesorder_id,
        salesorder_body,
        store=order.store,
    )
    persist_books_sales_order_line_item_ids(order, salesorder)

    salesorder_number = str(salesorder.get('salesorder_number') or '').strip()
    order.zoho_books_salesorder_error = ''
    update_fields = ['zoho_books_salesorder_error', 'updated_at']
    if salesorder_number and salesorder_number != (order.zoho_books_salesorder_number or '').strip():
        order.zoho_books_salesorder_number = salesorder_number[:64]
        update_fields.append('zoho_books_salesorder_number')
    order.save(update_fields=list(dict.fromkeys(update_fields)))
    logger.info(
        'zoho-books: sales order updated order=%s salesorder_id=%s',
        order.pk,
        salesorder_id,
    )
    return True


def maybe_update_zoho_books_sales_order_for_order(order_id: int) -> None:
    """Best-effort Books sales order update after local order edit. Never raises."""
    if not zoho_books_sales_order_enabled():
        return

    try:
        order = Order.objects.select_related('store').get(pk=order_id)
    except Order.DoesNotExist:
        return

    if not (order.zoho_books_salesorder_id or '').strip():
        maybe_create_zoho_books_sales_order_for_order(order_id, trigger='placed')
        return

    if order.status == Order.Status.CANCELLED:
        return

    try:
        update_zoho_books_sales_order_for_order(order)
    except ZohoBooksError as exc:
        logger.exception('zoho-books: sales order update failed order=%s (%s)', order_id, exc)
        Order.objects.filter(pk=order_id).update(
            zoho_books_salesorder_error=str(exc)[:5000],
            updated_at=dj_tz.now(),
        )
    except Exception as exc:
        logger.exception('zoho-books: unexpected sales order update error order=%s', order_id)
        Order.objects.filter(pk=order_id).update(
            zoho_books_salesorder_error=str(exc)[:5000],
            updated_at=dj_tz.now(),
        )


def void_zoho_books_sales_order_for_order(order: Order) -> bool:
    """Void an existing Zoho Books sales order."""
    order = Order.objects.select_related('store').get(pk=order.pk)
    salesorder_id = (order.zoho_books_salesorder_id or '').strip()
    if not salesorder_id:
        raise ZohoBooksError('Order has no Zoho Books sales order to void.')
    books_void_sales_order(salesorder_id, store=order.store)
    order.zoho_books_salesorder_error = ''
    order.save(update_fields=['zoho_books_salesorder_error', 'updated_at'])
    logger.info('zoho-books: sales order voided order=%s salesorder_id=%s', order.pk, salesorder_id)
    return True


def staff_cancel_zoho_books_order(order_id: int) -> tuple[bool, str]:
    """Staff: void Books sales order and cancel local order."""
    from shop.services.order_cancel import cancel_order

    return cancel_order(order_id, customer=False, notify=True)


def maybe_create_zoho_books_sales_order_for_order(order_id: int, *, trigger: str = 'synced') -> None:
    """Best-effort Books sales order creation; never raises to callers."""
    try:
        order = Order.objects.select_related('store').get(pk=order_id)
    except Order.DoesNotExist:
        return

    if not order_ready_for_books_sales_order(order, trigger=trigger):
        logger.info(
            'zoho-books: skip sales order order=%s trigger=%s enabled=%s',
            order_id,
            trigger,
            zoho_books_sales_order_enabled(),
        )
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
