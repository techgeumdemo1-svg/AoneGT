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

    # FIXED: Distribute product-level discounts (transaction / item / loyalty)
    # equally across non-shipping product line items at item level.
    # For item-level coupons, only eligible items get the discount — not all items equally.
    # Bxgy handled per-item above. Shipping never gets product discounts.
    if total_discount > Decimal('0') and not is_free_shipping_coupon and not is_bxgy_coupon:
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
            # FIXED: assign discount only to eligible product line items by matching zoho IDs.
            # Fetch the product records for order items so we can check their zoho IDs.
            from shop.models import OrderItem as OI
            order_items_map = {
                oi.pk: oi
                for oi in OI.objects.filter(order_id=order.pk).select_related('product')
            }
            # Match product_line_items (built in same order as OrderItem queryset) to order items.
            oi_list = list(OI.objects.filter(order_id=order.pk).select_related('product'))
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
            if eligible_indices:
                # FIXED: distribute total_discount only across eligible items.
                n_eligible = len(eligible_indices)
                per_eligible_discount = (total_discount / Decimal(str(n_eligible))).quantize(Decimal('0.01'))
                remainder = total_discount - (per_eligible_discount * n_eligible)
                for i, idx in enumerate(eligible_indices):
                    item_discount = per_eligible_discount + (remainder if i == 0 else Decimal('0'))
                    if item_discount > Decimal('0'):
                        product_line_items[idx]['discount'] = float(item_discount)
                        product_line_items[idx]['discount_type'] = 'item_level'
            else:
                # FIXED: fallback — no eligible items matched, equal split (should not happen normally).
                n_product_items = len(product_line_items)
                if n_product_items > 0:
                    per_item_discount = (total_discount / Decimal(str(n_product_items))).quantize(Decimal('0.01'))
                    remainder = total_discount - (per_item_discount * n_product_items)
                    for idx, row in enumerate(product_line_items):
                        item_discount = per_item_discount + (remainder if idx == 0 else Decimal('0'))
                        if item_discount > Decimal('0'):
                            row['discount'] = float(item_discount)
                            row['discount_type'] = 'item_level'
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
    return True


def update_zoho_books_sales_order_for_order(order: Order) -> bool:
    """Update an existing Zoho Books sales order from local order data."""
    order = Order.objects.select_related('user', 'store').prefetch_related('items').get(pk=order.pk)
    salesorder_id = (order.zoho_books_salesorder_id or '').strip()
    if not salesorder_id:
        raise ZohoBooksError('Order has no Zoho Books sales order to update.')

    customer_id = _resolve_customer_id(order)
    salesorder_body = _build_sales_order_payload(order, customer_id)
    salesorder = books_update_sales_order(salesorder_id, salesorder_body, store=order.store)
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
    """
    Staff: void Books sales order and cancel local order.
    Prepaid credit already on user account remains available for future orders.
    """
    from shop.models import AccountCreditLedger
    from shop.services.account_credit import get_user_credit_balance
    from shop.services.order_sync_state import apply_order_sync_transition

    try:
        order = Order.objects.select_related('user', 'store').get(pk=order_id)
    except Order.DoesNotExist:
        return False, 'Order not found.'

    if order.status == Order.Status.CANCELLED:
        return False, 'Order is already cancelled.'

    if (order.zoho_books_invoice_id or '').strip():
        return False, 'Cannot cancel: invoice already exists for this order.'

    try:
        with transaction.atomic():
            locked = Order.objects.select_for_update().select_related('user', 'store').get(pk=order_id)
            if locked.status == Order.Status.CANCELLED:
                return False, 'Order is already cancelled.'
            if (locked.zoho_books_invoice_id or '').strip():
                return False, 'Cannot cancel: invoice already exists for this order.'

            if (locked.zoho_books_salesorder_id or '').strip():
                void_zoho_books_sales_order_for_order(locked)

            apply_order_sync_transition(locked, Order.Status.CANCELLED)

            if (
                locked.payment_status == Order.PaymentStatus.PAID
                and Decimal(str(locked.prepaid_credited_amount or 0)).quantize(Decimal('0.01')) > 0
                and Decimal(str(locked.credit_applied_on_invoice or 0)).quantize(Decimal('0.01')) == 0
            ):
                AccountCreditLedger.objects.create(
                    user=locked.user,
                    order=locked,
                    kind=AccountCreditLedger.Kind.ORDER_CANCEL,
                    amount=Decimal('0'),
                    balance_after=get_user_credit_balance(locked.user),
                    note=(
                        f'Order #{locked.pk} cancelled; '
                        f'{locked.prepaid_credited_amount} AED remains on account credit.'
                    ),
                )
    except ZohoBooksError as exc:
        logger.exception('zoho-books: cancel failed order=%s (%s)', order_id, exc)
        Order.objects.filter(pk=order_id).update(
            zoho_books_salesorder_error=str(exc)[:5000],
            updated_at=dj_tz.now(),
        )
        return False, str(exc)
    except ValueError as exc:
        return False, str(exc)
    except Exception as exc:
        logger.exception('zoho-books: unexpected cancel error order=%s', order_id)
        return False, str(exc)

    return True, 'Order cancelled and Zoho Books sales order voided.'


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
