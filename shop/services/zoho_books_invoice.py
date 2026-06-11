"""Create a Zoho Books invoice when an order is confirmed."""

from __future__ import annotations

import logging
from datetime import timedelta
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

    # Zoho Books rejects the top-level 'shipping_charge' field for VAT-registered orgs
    # (HTTP 400: "Shipping and miscellaneous charges cannot be applied when registered for VAT.").
    # Always add shipping as a dedicated line item so the request succeeds regardless of whether
    # ZOHO_BOOKS_VAT_TAX_ID is configured.
    shipping_amount = Decimal(str(order.shipping_amount or 0))
    if shipping_amount > 0:
        line_items.append({
            'name': 'Shipping',
            'rate': float(shipping_amount),
            'quantity': 1.0,
        })

    payload: dict = {
        'customer_id': customer_id,
        'reference_number': order_code_for_order(order),
        'date': order.created_at.date().isoformat(),
        'line_items': line_items,
        'currency_code': (order.currency or 'AED').strip() or 'AED',
        'notes': _invoice_summary_notes(order),
    }

    from shop.services.zoho_books_payment import is_pay_on_delivery_payment_method

    if is_pay_on_delivery_payment_method(order.payment_method):
        # Zoho defaults due_date = invoice date when unset → shows "Due Today".
        # COD / card-on-delivery is collected at delivery, not at checkout.
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

    coupon_discount = _order_coupon_discount(order)
    loyalty_discount = Decimal(str(order.loyalty_discount or 0))
    total_discount = (coupon_discount + loyalty_discount).quantize(Decimal('0.01'))
    if total_discount > 0:
        payload['discount'] = float(total_discount)
        payload['discount_type'] = 'entity_level'
        payload['is_discount_before_tax'] = True

    tax_id = zoho_books_vat_tax_id(store=order.store)
    if tax_id:
        # Apply VAT tax to product line items only; shipping line item is VAT-exempt.
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

    # Branch 1 — stored contact id: validate it still exists in Zoho Books
    stored = (getattr(user, 'zoho_books_contact_id', '') or '').strip()
    if stored and books_get_contact(stored, store=store):
        books_update_contact_name(stored, name, store=store)
        return stored
    if stored:
        from accounts.models import User as UserModel
        UserModel.objects.filter(pk=user.pk).update(zoho_books_contact_id='')
        user.zoho_books_contact_id = ''

    # Branch 2 — search by email (most reliable, exact match)
    existing = books_find_contact_id_by_email(email, store=store) if email else None
    if existing:
        _persist_user_books_contact_id(user, existing)
        books_update_contact_name(existing, name, store=store)
        return existing

    # Branch 3 — search by name + email composite (both must match)
    existing_by_name = books_find_contact_id_by_name(name, store=store, email=email)
    if existing_by_name:
        _persist_user_books_contact_id(user, existing_by_name)
        books_update_contact_name(existing_by_name, name, store=store)
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
