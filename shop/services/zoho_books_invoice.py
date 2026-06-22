"""Create a Zoho Books invoice when an order is confirmed."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
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
    books_create_invoice_from_sales_order,
    books_find_contact_id_by_email,
    books_find_contact_id_by_name,
    books_get_contact,
    books_get_invoice,
    books_get_organization,
    books_get_sales_order,
    books_list_invoices_for_sales_order,
    books_mark_invoice_sent,
    books_update_contact_name,
    invoice_belongs_to_sales_order,
    store_has_books_config,
    zoho_books_enabled,
    zoho_books_vat_tax_id,
)

logger = logging.getLogger(__name__)


def zoho_books_manual_workflow() -> bool:
    """Staff-driven Books flow: SO at checkout, invoice + payment via staff endpoints."""
    return getattr(settings, 'ZOHO_BOOKS_MANUAL_WORKFLOW', False)


def _should_create_on_placed() -> bool:
    if zoho_books_manual_workflow():
        return False
    mode = (getattr(settings, 'ZOHO_BOOKS_CREATE_INVOICE_ON', 'placed') or 'placed').strip().lower()
    return mode in ('placed', 'both', 'checkout', 'confirmed')


def _should_create_on_synced() -> bool:
    if zoho_books_manual_workflow():
        return False
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
        f'Payment method - {order.get_payment_method_display()}',
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

    # FIXED: Retrieve coupon/loyalty discounts up-front to distribute at item level.
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
            # FIXED: derive per-item discount from stored line_total.
            # Get-item: line_total = unit_price * qty - discount. Buy-item: line_total = gross (no discount).
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

    # Distribute product-level discounts (transaction / item / loyalty) equally.
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
                    def _extract_id(x):
                        if isinstance(x, dict):
                            return str(x.get('product_id') or x.get('id') or x.get('category_id') or x.get('collection_id') or '').strip()
                        return str(x).strip()
                    item_coupon_eligible_zoho_ids = {_extract_id(x) for x in pid_list if _extract_id(x)}
                    item_coupon_eligible_category_ids = {_extract_id(x) for x in cat_list if _extract_id(x)}
                    item_coupon_eligible_collection_ids = {_extract_id(x) for x in col_list if _extract_id(x)}
                    is_item_coupon = True
        except Exception:
            pass

        if is_item_coupon and (item_coupon_eligible_zoho_ids or item_coupon_eligible_category_ids or item_coupon_eligible_collection_ids):
            # item coupon targets only eligible items; loyalty splits across ALL items.
            # Compute them separately and combine per item.
            from shop.models import OrderItem as OI
            oi_list = list(OI.objects.filter(order_id=order.pk).select_related('product'))
            n_all = len(oi_list)

            loyalty_per_item = (
                (loyalty_discount / Decimal(str(n_all))).quantize(Decimal('0.01'))
                if n_all > 0 else Decimal('0')
            )
            loyalty_remainder = loyalty_discount - (loyalty_per_item * n_all)

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

    # Zoho Books rejects top-level 'shipping_charge' for VAT-registered orgs.
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
            # FIXED: free_shipping — shipping gets full discount, tax stays Exempt.
            shipping_row['discount'] = float(shipping_amount)
            shipping_row['discount_type'] = 'item_level'
        line_items.append(shipping_row)

    payload: dict = {
        'customer_id': customer_id,
        'reference_number': order_code_for_order(order),
        'date': order.created_at.date().isoformat(),
        'line_items': line_items,
        'currency_code': (order.currency or 'AED').strip() or 'AED',
        'notes': _invoice_summary_notes(order),
        # FIXED: item_level so Zoho Books reads per-line discounts only.
        'discount_type': 'item_level',
    }
    # FIXED: NO top-level 'discount' key.

    from shop.services.zoho_books_payment import is_pay_on_delivery_payment_method

    if is_pay_on_delivery_payment_method(order.payment_method):
        raw_due_days = getattr(settings, 'ZOHO_BOOKS_PAY_ON_DELIVERY_DUE_DAYS', 7)
        try:
            due_days = int(raw_due_days)
        except (TypeError, ValueError):
            due_days = 7
        due_days = max(0, min(due_days, 100))
        invoice_date = order.created_at.date()
        payload['payment_terms'] = due_days
        payload['payment_terms_label'] = 'Due on Delivery'
        payload['due_date'] = (invoice_date + timedelta(days=due_days)).isoformat()

    tax_id = zoho_books_vat_tax_id(store=order.store)
    if tax_id:
        # Apply VAT tax to product line items only; shipping is VAT-exempt.
        for row in payload['line_items']:
            if row.get('name') != 'Shipping':
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

    # Compute display name early — used in all branches
    first = (getattr(user, 'first_name', '') or '').strip()
    last = (getattr(user, 'last_name', '') or '').strip()
    name = f'{first} {last}'.strip()
    if not name:
        name = (order.shipping_name or '').strip()
    if not name:
        name = email or f'Customer {user.pk}'

    # Branch 1 — stored contact id: use immediately on checkout (fast path).
    stored = (getattr(user, 'zoho_books_contact_id', '') or '').strip()
    if stored:
        from shop.services.checkout_async import schedule_books_contact_name_update

        schedule_books_contact_name_update(stored, name, store)
        return stored

    # Branch 2 — search by email (most reliable, exact match)
    existing = books_find_contact_id_by_email(email, store=store) if email else None
    if existing:
        _persist_user_books_contact_id(user, existing)
        from shop.services.checkout_async import schedule_books_contact_name_update

        schedule_books_contact_name_update(existing, name, store)
        return existing

    # Branch 3 — search by name + email composite (both must match)
    existing_by_name = books_find_contact_id_by_name(name, store=store, email=email)
    if existing_by_name:
        _persist_user_books_contact_id(user, existing_by_name)
        from shop.services.checkout_async import schedule_books_contact_name_update

        schedule_books_contact_name_update(existing_by_name, name, store)
        return existing_by_name

    # Branch 4 — create new contact
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

    try:
        contact_id = books_create_contact(
            contact_name=name,
            email=email,
            phone=phone,
            billing_address=billing_address,
            store=store,
        )
        _persist_user_books_contact_id(user, contact_id)
        return contact_id

    except ZohoBooksError as exc:
        if 'already exists' not in str(exc).lower():
            raise

        # Step A — retry email search: handles concurrent checkout race
        if email:
            retry_email = books_find_contact_id_by_email(email, store=store)
            if retry_email:
                _persist_user_books_contact_id(user, retry_email)
                books_update_contact_name(retry_email, name, store=store)
                return retry_email

        # Step B — same name, different user: create with unique name
        unique_name = f'{name} ({email})' if email else f'{name} ({user.pk})'
        unique_name = unique_name[:200]

        try:
            contact_id = books_create_contact(
                contact_name=unique_name,
                email=email,
                phone=phone,
                billing_address={**billing_address, 'attention': unique_name[:100]},
                store=store,
            )
            _persist_user_books_contact_id(user, contact_id)
            return contact_id

        except ZohoBooksError as exc2:
            if 'already exists' not in str(exc2).lower():
                raise

            # Step C — unique name also exists: retry email search one final time
            if email:
                final_retry = books_find_contact_id_by_email(email, store=store)
                if final_retry:
                    _persist_user_books_contact_id(user, final_retry)
                    books_update_contact_name(final_retry, name, store=store)
                    return final_retry

            # Step D — search by unique name as last resort
            final_by_name = books_find_contact_id_by_name(
                unique_name, store=store, email=email
            )
            if final_by_name:
                _persist_user_books_contact_id(user, final_by_name)
                return final_by_name

            raise


def create_zoho_books_invoice_for_order(order: Order) -> bool:
    """
    Create invoice in Zoho Books and persist ids on the order.
    Returns True on success. Raises ZohoBooksError on API failure.
    """
    order = Order.objects.select_related('user', 'store').prefetch_related('items').get(pk=order.pk)
    salesorder_id = (order.zoho_books_salesorder_id or '').strip()
    invoice_from_sales_order = getattr(settings, 'ZOHO_BOOKS_INVOICE_FROM_SALES_ORDER', True)

    if salesorder_id and invoice_from_sales_order:
        invoice = books_create_invoice_from_sales_order(
            salesorder_id,
            store=order.store,
            json_data={'date': order.created_at.date().isoformat()},
        )
    else:
        customer_id = _resolve_customer_id(order)
        invoice_body = _build_invoice_payload(order, customer_id)
        invoice = books_create_invoice(invoice_body, store=order.store)

    invoice_id = str(invoice.get('invoice_id') or '').strip()
    invoice_number = str(invoice.get('invoice_number') or '').strip()
    if not invoice_id:
        raise ZohoBooksError('Zoho Books invoice_id missing in response.')

    invoice_error = ''
    from shop.services.zoho_books_payment import is_pay_on_delivery_payment_method

    if is_pay_on_delivery_payment_method(order.payment_method):
        try:
            books_mark_invoice_sent(invoice_id, store=order.store)
            logger.info(
                'zoho-books: invoice marked sent order=%s invoice_id=%s',
                order.pk,
                invoice_id,
            )
        except ZohoBooksError as exc:
            logger.exception(
                'zoho-books: mark sent failed order=%s invoice_id=%s',
                order.pk,
                invoice_id,
            )
            invoice_error = f'Invoice created but could not mark sent: {exc}'[:5000]

    order.zoho_books_invoice_id = invoice_id[:64]
    order.zoho_books_invoice_number = invoice_number[:64]
    order.zoho_books_invoice_error = invoice_error
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


def maybe_finalize_zoho_books_invoice_for_order(order_id: int, *, trigger: str = 'synced') -> None:
    """
    Legacy automatic pipeline on order confirm (``trigger='synced'``): create Books SO (if enabled),
    invoice, then prepaid payment or mark sent for COD.

    Skipped when ``ZOHO_BOOKS_MANUAL_WORKFLOW`` is enabled — staff create invoice and payment separately.
    """
    if zoho_books_manual_workflow():
        return

    from shop.services.zoho_books_payment import (
        is_prepaid_at_checkout_payment_method,
        maybe_record_zoho_books_payment_for_order,
    )
    from shop.services.zoho_books_sales_order import maybe_create_zoho_books_sales_order_for_order

    maybe_create_zoho_books_sales_order_for_order(order_id, trigger=trigger)
    maybe_create_zoho_books_invoice(order_id, trigger=trigger)

    order = (
        Order.objects.filter(pk=order_id)
        .only('pk', 'payment_method', 'zoho_books_invoice_id')
        .first()
    )
    if not order or not (order.zoho_books_invoice_id or '').strip():
        return

    if is_prepaid_at_checkout_payment_method(order.payment_method):
        maybe_record_zoho_books_payment_for_order(order_id)
    # cash_on_delivery / card_on_delivery: invoice marked sent in create_zoho_books_invoice_for_order.


def staff_create_zoho_books_invoice_for_order(order_id: int) -> tuple[bool, str]:
    """
    Staff-triggered invoice creation from an existing Books sales order.
    Returns (success, message). Raises ZohoBooksError only when called outside try/except.
    """
    if not zoho_books_enabled():
        return False, 'Zoho Books is disabled.'
    try:
        order = Order.objects.select_related('store').get(pk=order_id)
    except Order.DoesNotExist:
        return False, 'Order not found.'
    if order.status == Order.Status.CANCELLED:
        return False, 'Cancelled orders cannot be invoiced.'
    if not store_has_books_config(order.store):
        return False, 'Store is missing Zoho Books org configuration.'
    if not (order.zoho_books_salesorder_id or '').strip():
        return False, 'Order has no Zoho Books sales order yet.'
    if (order.zoho_books_invoice_id or '').strip():
        return False, 'Invoice already exists for this order.'

    from shop.services.zoho_books_payment import (
        is_pay_on_delivery_payment_method,
        is_prepaid_at_checkout_payment_method,
        record_zoho_books_payment_for_order,
    )

    if is_prepaid_at_checkout_payment_method(order.payment_method):
        if order.payment_status != Order.PaymentStatus.PAID:
            return False, 'Prepaid order must be paid before creating an invoice.'

    try:
        with transaction.atomic():
            locked = (
                Order.objects.select_for_update()
                .select_related('store', 'user')
                .get(pk=order_id)
            )
            if (locked.zoho_books_invoice_id or '').strip():
                return False, 'Invoice already exists for this order.'
            create_zoho_books_invoice_for_order(locked)
            locked.refresh_from_db()

            credit_msg = ''
            if is_prepaid_at_checkout_payment_method(locked.payment_method):
                from shop.services.account_credit import apply_prepaid_credit_on_invoice

                applied, remainder = apply_prepaid_credit_on_invoice(locked)
                locked.refresh_from_db()
                if applied > 0 and not (locked.zoho_books_payment_id or '').strip():
                    record_zoho_books_payment_for_order(
                        locked,
                        amount=applied,
                        gateway_reference=locked.gateway_reference,
                    )
                credit_msg = (
                    f' Credit applied: {applied} AED; '
                    f'{remainder} AED remains on user account.'
                )
            elif is_pay_on_delivery_payment_method(locked.payment_method):
                credit_msg = ' Record payment when delivered via the payment endpoint.'
    except ZohoBooksError as exc:
        logger.exception('zoho-books: staff invoice failed order=%s (%s)', order_id, exc)
        Order.objects.filter(pk=order_id).update(
            zoho_books_invoice_error=str(exc)[:5000],
            updated_at=dj_tz.now(),
        )
        return False, str(exc)
    except Exception as exc:
        logger.exception('zoho-books: staff invoice unexpected error order=%s', order_id)
        Order.objects.filter(pk=order_id).update(
            zoho_books_invoice_error=str(exc)[:5000],
            updated_at=dj_tz.now(),
        )
        return False, str(exc)

    return True, f'Zoho Books invoice created.{credit_msg}'


_VOID_INVOICE_STATUSES = frozenset({'void', 'cancelled'})


def _clear_linked_invoice_fields(order: Order) -> None:
    order.zoho_books_invoice_id = ''
    order.zoho_books_invoice_number = ''
    order.zoho_books_invoice_error = ''
    order.zoho_books_invoiced_at = None


def _pick_invoice_for_order(order: Order, invoices: list[dict]) -> dict | None:
    """Choose the best Zoho invoice for this order's sales order."""
    order_total = Decimal(str(order.total or 0)).quantize(Decimal('0.01'))
    salesorder_id = (order.zoho_books_salesorder_id or '').strip()
    candidates: list[dict] = []
    for invoice in invoices:
        if not isinstance(invoice, dict):
            continue
        status = (invoice.get('status') or '').strip().lower()
        if status in _VOID_INVOICE_STATUSES:
            continue
        if not invoice_belongs_to_sales_order(invoice, salesorder_id):
            continue
        candidates.append(invoice)

    if not candidates:
        return None

    unpaid = [
        row for row in candidates
        if Decimal(str(row.get('balance', 0))).quantize(Decimal('0.01')) > 0
    ]
    pool = unpaid or candidates

    def sort_key(row: dict) -> tuple:
        total = Decimal(str(row.get('total', 0))).quantize(Decimal('0.01'))
        balance = Decimal(str(row.get('balance', 0))).quantize(Decimal('0.01'))
        return (abs(total - order_total), -balance)

    return min(pool, key=sort_key)


def relink_zoho_books_invoice_for_order_from_books(order: Order) -> tuple[bool, str]:
    """
    Drop a stale/wrong local invoice link and sync from Zoho Books again.
    """
    if (order.zoho_books_invoice_id or '').strip():
        _clear_linked_invoice_fields(order)
        order.save(
            update_fields=[
                'zoho_books_invoice_id',
                'zoho_books_invoice_number',
                'zoho_books_invoice_error',
                'zoho_books_invoiced_at',
                'updated_at',
            ],
        )
    return sync_zoho_books_invoice_for_order_from_books(order)


def ensure_zoho_books_invoice_for_order(order: Order) -> tuple[bool, str]:
    """
    Ensure the local order points at the correct Zoho invoice for its sales order.
    Clears and re-links when the stored invoice belongs to another sales order.
    """
    salesorder_id = (order.zoho_books_salesorder_id or '').strip()
    if not salesorder_id:
        return False, 'Order has no Zoho Books sales order.'

    linked_id = (order.zoho_books_invoice_id or '').strip()
    if linked_id:
        try:
            invoice = books_get_invoice(linked_id, store=order.store)
        except ZohoBooksError as exc:
            return relink_zoho_books_invoice_for_order_from_books(order)

        if invoice_belongs_to_sales_order(invoice, salesorder_id):
            return True, 'Zoho Books invoice already linked.'

        logger.warning(
            'zoho-books: wrong invoice linked order=%s invoice=%s salesorder=%s',
            order.pk,
            linked_id,
            salesorder_id,
        )
        return relink_zoho_books_invoice_for_order_from_books(order)

    return sync_zoho_books_invoice_for_order_from_books(order)


def sync_zoho_books_invoice_for_order_from_books(order: Order) -> tuple[bool, str]:
    """
    Link an invoice staff created in the Zoho Books UI to this local order.
    Looks up invoices by sales order id — no invoice is created by our API.
    """
    if not zoho_books_enabled():
        return False, 'Zoho Books is disabled.'
    if not store_has_books_config(order.store):
        return False, 'Store is missing Zoho Books org configuration.'
    if order.status == Order.Status.CANCELLED:
        return False, 'Cancelled orders cannot be linked to an invoice.'
    if (order.zoho_books_invoice_id or '').strip():
        return True, 'Zoho Books invoice already linked.'

    salesorder_id = (order.zoho_books_salesorder_id or '').strip()
    if not salesorder_id:
        return False, 'Order has no Zoho Books sales order.'

    try:
        invoices = books_list_invoices_for_sales_order(salesorder_id, store=order.store)
    except ZohoBooksError as exc:
        logger.warning(
            'zoho-books: invoice sync failed order=%s salesorder=%s (%s)',
            order.pk,
            salesorder_id,
            exc,
        )
        return False, str(exc)

    candidate = _pick_invoice_for_order(order, invoices)

    if candidate is None:
        so_number = (order.zoho_books_salesorder_number or '').strip()
        so_label = f' {so_number}' if so_number else ''
        try:
            salesorder = books_get_sales_order(salesorder_id, store=order.store)
            invoiced_status = str(salesorder.get('invoiced_status') or '').strip()
        except ZohoBooksError:
            invoiced_status = ''
        if invoiced_status == 'not_invoiced':
            return (
                False,
                f'No invoice found in Zoho Books for sales order{so_label}. '
                'The sales order is confirmed, but an invoice has not been created yet. '
                f'In Zoho Books, open{so_label} and use Convert to Invoice, then retry collect-cod.',
            )
        return (
            False,
            'No invoice found in Zoho Books for this sales order. '
            'Staff must confirm the sales order and create the invoice in Zoho Books.',
        )

    invoice_id = str(candidate.get('invoice_id') or '').strip()
    invoice_number = str(candidate.get('invoice_number') or '').strip()
    if not invoice_id:
        return False, 'Zoho Books returned an invoice without invoice_id.'

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
        'zoho-books: linked invoice from Books order=%s invoice_id=%s number=%s',
        order.pk,
        invoice_id,
        invoice_number,
    )
    return True, f'Linked Zoho Books invoice {invoice_number or invoice_id}.'


def _invoice_money(value, *, precision: int = 2) -> str:
    return str(Decimal(str(value or 0)).quantize(Decimal('1.' + '0' * precision)))


def _invoice_money_display(value, currency: str, *, precision: int = 2) -> str:
    amount = _invoice_money(value, precision=precision)
    return f'{currency}{amount}'


def _invoice_payment_display(value, currency: str, *, precision: int = 2) -> str:
    amount = _invoice_money(value, precision=precision)
    if Decimal(amount) == 0:
        return f'{currency}0.00'
    return f'(-) {amount}'


def _invoice_format_date(raw) -> str:
    text = (str(raw or '')).strip()
    if not text:
        return ''
    for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(text[:10], fmt).strftime('%d %b %Y')
        except ValueError:
            continue
    return text


def _invoice_join_address_parts(*parts) -> str:
    seen: list[str] = []
    for part in parts:
        piece = (part or '').strip()
        if piece and piece not in seen:
            seen.append(piece)
    return ', '.join(seen)


def _invoice_billing_address_lines(invoice: dict, order: Order | None = None) -> list[str]:
    billing = invoice.get('billing_address') or {}
    if isinstance(billing, dict):
        line = _invoice_join_address_parts(
            billing.get('address'),
            billing.get('street2'),
            billing.get('city'),
            billing.get('state'),
            billing.get('country'),
        )
        if line:
            return [line]
    if order is not None:
        line = _invoice_join_address_parts(
            order.shipping_address,
            order.shipping_city,
            order.shipping_state,
            order.shipping_country,
        )
        if line:
            return [line]
    return []


def _invoice_default_vat_percent(order: Order) -> Decimal:
    return Decimal(str(order.vat_percent or 5)).quantize(Decimal('0.01'))


def _invoice_line_vat_percent(row: dict, order: Order) -> Decimal:
    if (row.get('name') or '').strip().lower() == 'shipping':
        return Decimal('0.00')
    raw = row.get('tax_percentage')
    if raw is not None and str(raw).strip() != '':
        return Decimal(str(raw)).quantize(Decimal('0.01'))
    return _invoice_default_vat_percent(order)


def _invoice_seller_payload(organization: dict | None, *, store) -> dict:
    if organization:
        address = organization.get('address') or {}
        street = _invoice_join_address_parts(
            address.get('street_address1'),
            address.get('street_address2'),
            address.get('address'),
        )
        city_state = _invoice_join_address_parts(address.get('city'), address.get('state'))
        country = (address.get('country') or '').strip()
        address_lines = [row for row in (street, city_state, country) if row]
        if not address_lines:
            org_address = (organization.get('org_address') or '').strip()
            if org_address:
                address_lines = [org_address]
        trn_label = (
            organization.get('tax_id_label')
            or organization.get('taxid_label')
            or 'TRN'
        )
        trn = organization.get('tax_id_value') or organization.get('taxid_value') or ''
        return {
            'name': (organization.get('name') or getattr(store, 'name', '') or '').strip(),
            'address_lines': address_lines,
            'trn': str(trn).strip(),
            'trn_label': str(trn_label).strip() or 'TRN',
            'phone': (organization.get('phone') or '').strip(),
            'email': (organization.get('email') or '').strip(),
        }
    return {
        'name': (getattr(store, 'name', '') or '').strip(),
        'address_lines': [],
        'trn': '',
        'trn_label': 'TRN',
        'phone': '',
        'email': (getattr(store, 'contact_email', '') or '').strip(),
    }


def _invoice_line_tax_amount(row: dict, *, order: Order | None = None) -> Decimal:
    raw = row.get('tax_amount')
    if raw is not None and str(raw).strip() != '':
        return Decimal(str(raw)).quantize(Decimal('0.01'))
    pct = Decimal(str(row.get('tax_percentage') or 0))
    if pct <= 0 and order is not None:
        pct = _invoice_line_vat_percent(row, order)
    taxable = Decimal(str(row.get('item_total') or 0)).quantize(Decimal('0.01'))
    if pct > 0 and taxable > 0:
        return (taxable * pct / Decimal('100')).quantize(Decimal('0.01'))
    return Decimal('0.00')


def _invoice_tax_summary_label(row: dict, order: Order | None = None) -> str:
    pct = Decimal(str(row.get('tax_percentage') or 0))
    if pct <= 0 and order is not None:
        pct = _invoice_line_vat_percent(row, order)
    pct = pct.quantize(Decimal('0.01'))
    tax_name = (row.get('tax_name') or '').strip()
    if pct <= 0 and not tax_name:
        return 'Exempt'
    if tax_name:
        if '(' in tax_name:
            return tax_name
        if pct > 0:
            return f'{tax_name} ({pct}%)'
        return tax_name
    return f'Standard Rate ({pct}%)'


def _invoice_build_tax_summary(line_items: list[dict], *, order: Order | None = None) -> list[dict]:
    buckets: dict[str, dict] = {}
    for row in line_items:
        if not isinstance(row, dict):
            continue
        label = _invoice_tax_summary_label(row, order)
        taxable = Decimal(str(row.get('item_total') or 0)).quantize(Decimal('0.01'))
        tax_amount = _invoice_line_tax_amount(row, order=order)
        bucket = buckets.setdefault(
            label,
            {'label': label, 'taxable_amount': Decimal('0.00'), 'tax_amount': Decimal('0.00')},
        )
        bucket['taxable_amount'] += taxable
        bucket['tax_amount'] += tax_amount
    summary = []
    for bucket in buckets.values():
        summary.append({
            'label': bucket['label'],
            'taxable_amount': _invoice_money(bucket['taxable_amount']),
            'tax_amount': _invoice_money(bucket['tax_amount']),
        })
    return summary


def _invoice_status_display(raw_status: str) -> str:
    status = (raw_status or '').strip().lower()
    if not status:
        return ''
    return status.replace('_', ' ').title()


def build_zoho_books_invoice_detail_payload(
    order: Order,
    invoice: dict,
    *,
    organization: dict | None = None,
) -> dict:
    """
    Structured tax-invoice view (seller, bill-to, line items, totals, tax summary).
    """
    currency = (
        (invoice.get('currency_code') or order.currency or 'AED').strip() or 'AED'
    )
    precision = int(invoice.get('price_precision') or 2)
    default_vat_percent = _invoice_default_vat_percent(order)
    line_items_raw = [
        row for row in (invoice.get('line_items') or []) if isinstance(row, dict)
    ]
    line_items: list[dict] = []
    subtotal_taxable = Decimal('0.00')
    subtotal_tax = Decimal('0.00')
    subtotal_amount = Decimal('0.00')

    for index, row in enumerate(line_items_raw, start=1):
        quantity = Decimal(str(row.get('quantity') or 0)).quantize(Decimal('0.01'))
        rate = Decimal(str(row.get('rate') or 0)).quantize(Decimal('0.01'))
        taxable_amount = Decimal(str(row.get('item_total') or 0)).quantize(Decimal('0.01'))
        if taxable_amount <= 0 and quantity > 0 and rate > 0:
            taxable_amount = (quantity * rate).quantize(Decimal('0.01'))
        tax_percent = _invoice_line_vat_percent(row, order)
        tax_amount = _invoice_line_tax_amount(row, order=order)
        line_total = (taxable_amount + tax_amount).quantize(Decimal('0.01'))
        subtotal_taxable += taxable_amount
        subtotal_tax += tax_amount
        subtotal_amount += line_total
        line_items.append({
            'line_number': index,
            'name': (row.get('name') or '').strip(),
            'description': (row.get('description') or row.get('sku') or '').strip(),
            'quantity': _invoice_money(quantity, precision=2),
            'rate': _invoice_money(rate, precision=2),
            'taxable_amount': _invoice_money(taxable_amount, precision=2),
            'tax_percent': _invoice_money(tax_percent, precision=2) if tax_percent > 0 else '',
            'tax_amount': _invoice_money(tax_amount, precision=2) if tax_amount > 0 else '',
            'tax_display': _invoice_money(tax_amount, precision=2) if tax_amount > 0 else '-',
            'amount': _invoice_money(line_total, precision=2),
        })

    invoice_number = (invoice.get('invoice_number') or order.zoho_books_invoice_number or '').strip()
    balance_due = Decimal(str(invoice.get('balance', 0) or 0)).quantize(Decimal('0.01'))
    payment_made = Decimal(str(invoice.get('payment_made', 0) or 0)).quantize(Decimal('0.01'))
    total = Decimal(str(invoice.get('total', 0) or 0)).quantize(Decimal('0.01'))
    tax_total = Decimal(str(invoice.get('tax_total', 0) or 0)).quantize(Decimal('0.01'))
    sub_total = Decimal(str(invoice.get('sub_total', 0) or 0)).quantize(Decimal('0.01'))
    if sub_total <= 0:
        sub_total = subtotal_taxable
    if tax_total <= 0:
        tax_total = subtotal_tax
    if total <= 0:
        total = subtotal_amount
    if payment_made <= 0 and total > 0 and balance_due <= 0:
        payment_made = total

    tax_summary = _invoice_build_tax_summary(line_items_raw, order=order)
    tax_summary_taxable = sum(
        Decimal(row['taxable_amount']) for row in tax_summary
    ).quantize(Decimal('0.01'))
    tax_summary_tax = sum(
        Decimal(row['tax_amount']) for row in tax_summary
    ).quantize(Decimal('0.01'))

    sales_order_number = (
        (invoice.get('salesorder_number') or order.zoho_books_salesorder_number or '').strip()
    )
    raw_status = (invoice.get('status') or '').strip()
    if balance_due <= 0 and payment_made > 0 and raw_status not in ('void', 'cancelled'):
        status = 'paid'
        status_display = 'Paid'
    else:
        status = raw_status or 'sent'
        status_display = _invoice_status_display(status)

    customer_name = (invoice.get('customer_name') or order.shipping_name or '').strip()
    if not customer_name and order.user_id:
        first = (getattr(order.user, 'first_name', '') or '').strip()
        last = (getattr(order.user, 'last_name', '') or '').strip()
        customer_name = f'{first} {last}'.strip()

    invoice_date_iso = (invoice.get('date') or '')[:10]
    due_date_iso = (invoice.get('due_date') or '')[:10]
    if not invoice_date_iso and order.zoho_books_invoiced_at:
        invoice_date_iso = order.zoho_books_invoiced_at.date().isoformat()
    terms = (invoice.get('payment_terms_label') or '').strip()
    if not terms:
        from shop.services.zoho_books_payment import is_pay_on_delivery_payment_method

        terms = 'Due on Delivery' if is_pay_on_delivery_payment_method(order.payment_method) else 'Due on Receipt'
    if not due_date_iso and invoice_date_iso:
        from shop.services.zoho_books_payment import is_pay_on_delivery_payment_method

        if is_pay_on_delivery_payment_method(order.payment_method):
            due_days = int(getattr(settings, 'ZOHO_BOOKS_PAY_ON_DELIVERY_DUE_DAYS', 7) or 7)
            due_date_iso = (
                datetime.strptime(invoice_date_iso, '%Y-%m-%d').date() + timedelta(days=due_days)
            ).isoformat()
        else:
            due_date_iso = invoice_date_iso

    return {
        'document_title': 'TAX INVOICE',
        'status': status,
        'status_display': status_display,
        'currency': currency,
        'vat_percent': _invoice_money(default_vat_percent, precision=2),
        'invoice_id': str(invoice.get('invoice_id') or order.zoho_books_invoice_id or '').strip(),
        'invoice_number': invoice_number,
        'invoice_number_display': f'# {invoice_number}' if invoice_number else '',
        'balance_due': _invoice_money(balance_due, precision=precision),
        'balance_due_display': _invoice_money_display(balance_due, currency, precision=precision),
        'seller': _invoice_seller_payload(organization, store=order.store),
        'bill_to': {
            'name': customer_name,
            'address_lines': _invoice_billing_address_lines(invoice, order),
        },
        'invoice_date': _invoice_format_date(invoice_date_iso),
        'invoice_date_iso': invoice_date_iso,
        'terms': terms,
        'due_date': _invoice_format_date(due_date_iso),
        'due_date_iso': due_date_iso,
        'sales_order_number': sales_order_number,
        'order_id': order.pk,
        'order_code': order_code_for_order(order),
        'line_items': line_items,
        'summary': {
            'subtotal': {
                'taxable_amount': _invoice_money(sub_total, precision=precision),
                'tax': _invoice_money(tax_total, precision=precision),
                'amount': _invoice_money(total, precision=precision),
            },
            'total': _invoice_money(total, precision=precision),
            'total_display': _invoice_money_display(total, currency, precision=precision),
            'payment_made': _invoice_money(payment_made, precision=precision),
            'payment_made_display': _invoice_payment_display(payment_made, currency, precision=precision),
            'balance_due': _invoice_money(balance_due, precision=precision),
            'balance_due_display': _invoice_money_display(balance_due, currency, precision=precision),
        },
        'tax_summary': tax_summary,
        'tax_summary_totals': {
            'taxable_amount': _invoice_money(tax_summary_taxable, precision=precision),
            'tax_amount': _invoice_money(tax_summary_tax, precision=precision),
            'taxable_amount_display': _invoice_money_display(
                tax_summary_taxable,
                currency,
                precision=precision,
            ),
            'tax_amount_display': _invoice_money_display(
                tax_summary_tax,
                currency,
                precision=precision,
            ),
        },
        'notes': (invoice.get('notes') or '').strip(),
        'invoice_url': (invoice.get('invoice_url') or '').strip(),
    }


def fetch_zoho_books_invoice_detail_for_order(order: Order) -> dict:
    """Load invoice + organization from Zoho Books and return the detail payload."""
    invoice_id = (order.zoho_books_invoice_id or '').strip()
    if not invoice_id:
        raise ZohoBooksError('Order has no linked Zoho Books invoice.')
    invoice = books_get_invoice(invoice_id, store=order.store)
    organization = None
    try:
        organization = books_get_organization(store=order.store)
    except ZohoBooksError as exc:
        logger.warning(
            'zoho-books: organization fetch failed order=%s (%s)',
            order.pk,
            exc,
        )
    return build_zoho_books_invoice_detail_payload(
        order,
        invoice,
        organization=organization,
    )


def build_invoice_detail_fallback_from_order(order: Order) -> dict:
    """Build invoice-shaped payload from the local order when Zoho fetch is unavailable."""
    from shop.models import OrderItem
    from shop.services.zoho_books_payment import is_pay_on_delivery_payment_method

    currency = (order.currency or 'AED').strip() or 'AED'
    vat_percent = _invoice_default_vat_percent(order)
    organization = None
    try:
        organization = books_get_organization(store=order.store)
    except ZohoBooksError:
        pass

    zoho_line_items: list[dict] = []
    for item in OrderItem.objects.filter(order_id=order.pk):
        qty = float(item.quantity or 0)
        rate = float(Decimal(str(item.unit_price or 0)))
        row = {
            'name': (item.product_name or 'Item')[:200],
            'description': (item.sku or '')[:200],
            'quantity': qty,
            'rate': rate,
            'item_total': float(Decimal(str(item.line_total or 0))),
            'tax_percentage': float(vat_percent),
        }
        if row['item_total'] <= 0 and qty > 0 and rate > 0:
            row['item_total'] = float((Decimal(str(qty)) * Decimal(str(rate))).quantize(Decimal('0.01')))
        zoho_line_items.append(row)

    shipping_amount = Decimal(str(order.shipping_amount or 0)).quantize(Decimal('0.01'))
    if shipping_amount > 0:
        zoho_line_items.append({
            'name': 'Shipping',
            'quantity': 1.0,
            'rate': float(shipping_amount),
            'item_total': float(shipping_amount),
            'tax_percentage': 0.0,
        })

    invoice_dt = order.zoho_books_invoiced_at or order.created_at
    invoice_date_iso = invoice_dt.date().isoformat() if invoice_dt else ''
    if is_pay_on_delivery_payment_method(order.payment_method):
        terms = 'Due on Delivery'
        due_days = int(getattr(settings, 'ZOHO_BOOKS_PAY_ON_DELIVERY_DUE_DAYS', 7) or 7)
        due_date_iso = (
            invoice_dt.date() + timedelta(days=due_days)
        ).isoformat() if invoice_dt else ''
        status = 'sent'
    else:
        terms = 'Due on Receipt'
        due_date_iso = invoice_date_iso
        status = 'paid' if order.payment_status == Order.PaymentStatus.PAID else 'sent'

    invoice_stub = {
        'invoice_id': order.zoho_books_invoice_id,
        'invoice_number': order.zoho_books_invoice_number,
        'currency_code': currency,
        'date': invoice_date_iso,
        'due_date': due_date_iso,
        'payment_terms_label': terms,
        'status': status,
        'line_items': zoho_line_items,
        'sub_total': float(Decimal(str(order.subtotal or 0))),
        'tax_total': float(Decimal(str(order.vat_amount or 0))),
        'total': float(Decimal(str(order.total or 0))),
        'balance': float(Decimal(str(order.total or 0))),
        'payment_made': 0.0,
        'salesorder_number': order.zoho_books_salesorder_number or '',
        'notes': '',
    }
    if order.payment_status == Order.PaymentStatus.PAID:
        invoice_stub['balance'] = 0.0
        invoice_stub['payment_made'] = invoice_stub['total']

    detail = build_zoho_books_invoice_detail_payload(
        order,
        invoice_stub,
        organization=organization,
    )
    detail['source'] = 'order_fallback'
    return detail


def resolve_invoice_detail_for_order(order: Order) -> tuple[dict | None, str | None]:
    """
    Return invoice detail for API responses.

    Tries Zoho Books first; falls back to local order data if the fetch fails.
    """
    if not (order.zoho_books_invoice_id or '').strip():
        return None, None
    try:
        detail = fetch_zoho_books_invoice_detail_for_order(order)
        detail['source'] = 'zoho_books'
        return detail, None
    except Exception as exc:
        logger.warning(
            'zoho-books: invoice detail fetch failed order=%s (%s)',
            order.pk,
            exc,
        )
        try:
            detail = build_invoice_detail_fallback_from_order(order)
            return detail, str(exc)
        except Exception as fallback_exc:
            logger.exception(
                'zoho-books: invoice detail fallback failed order=%s',
                order.pk,
            )
            return None, f'{exc}; fallback failed: {fallback_exc}'
