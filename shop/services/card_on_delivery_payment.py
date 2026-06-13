"""Card on delivery: Geidea payment at door, then delivered + Zoho invoice paid."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone as dj_tz

from shop.models import Order
from shop.services.geidea import fetch_geidea_orders_by_merchant_ref
from shop.services.order_delivery_payment import maybe_auto_mark_delivered_on_payment
from shop.services.zoho_books import (
    ZohoBooksError,
    books_get_invoice,
    books_list_invoices_for_sales_order,
    invoice_belongs_to_sales_order,
)
from shop.services.zoho_books_invoice import ensure_zoho_books_invoice_for_order
from shop.services.zoho_books_payment import staff_record_zoho_books_payment_for_order

logger = logging.getLogger(__name__)

COLLECTABLE_TRACKING_STAGES = frozenset({
    Order.CustomerTrackingStage.OUT_FOR_DELIVERY,
})

_SUCCESS_TRANSACTION_STATUSES = frozenset({
    'paid',
    'success',
    'successful',
    'completed',
    'approved',
})


def is_card_on_delivery_order(order: Order) -> bool:
    return order.payment_method == Order.PaymentMethod.CARD_ON_DELIVERY


def _transaction_status_successful(raw_status: str) -> bool:
    return (raw_status or '').strip().lower() in _SUCCESS_TRANSACTION_STATUSES


def paid_geidea_entry(orders_list: list) -> dict | None:
    """Return the first successful paid Geidea order entry, if any."""
    return next(
        (
            entry
            for entry in orders_list
            if entry.get('status') == 'Success' and entry.get('detailedStatus') == 'Paid'
        ),
        None,
    )


def find_geidea_paid_entry_for_order(
    order: Order,
    *,
    gateway_reference: str = '',
) -> tuple[dict | None, str]:
    """
    Verify a Geidea POS/HPP payment against the order's merchant reference.
    """
    merchant_ref = str(order.geidea_merchant_ref or '').strip()
    if not merchant_ref:
        return None, (
            'No Geidea merchant reference on this order. '
            'Call POST /api/admin/orders/geidea-collect/?id=<order_id> before collecting payment.'
        )

    orders_list = fetch_geidea_orders_by_merchant_ref(merchant_ref)
    if not orders_list:
        return None, (
            'No Geidea payment found for this order. '
            'Complete card payment on the Geidea device using the session from '
            'POST /api/admin/orders/geidea-collect/?id=<order_id>, then call '
            'collect-card again (or geidea-reconcile if the callback was missed).'
        )

    gateway_reference = (gateway_reference or '').strip()
    if gateway_reference:
        entry = next(
            (row for row in orders_list if str(row.get('orderId') or '').strip() == gateway_reference),
            None,
        )
        if entry is None:
            return None, 'Geidea payment reference was not found for this order.'
    else:
        entry = paid_geidea_entry(orders_list)
        if entry is None:
            return None, 'No successful Geidea payment found for this order.'

    if entry.get('status') != 'Success' or entry.get('detailedStatus') != 'Paid':
        return None, 'Geidea payment was not successful.'

    geidea_amount = Decimal(str(entry.get('totalAmount', 0))).quantize(Decimal('0.01'))
    order_total = Decimal(str(order.total or 0)).quantize(Decimal('0.01'))
    if geidea_amount != order_total:
        return None, (
            f'Payment amount mismatch. Expected {order_total}, Geidea reported {geidea_amount}.'
        )

    return entry, ''


def order_ready_for_card_on_delivery_collect(order: Order) -> tuple[bool, str]:
    if not is_card_on_delivery_order(order):
        return False, 'Order is not card on delivery.'
    if order.status == Order.Status.CANCELLED:
        return False, 'Order is cancelled.'
    if order.payment_status == Order.PaymentStatus.PAID:
        return False, 'Order is already paid.'
    if order.payment_status != Order.PaymentStatus.PENDING:
        return False, 'Order is not awaiting card payment.'
    stage = (order.customer_tracking_stage or '').strip()
    if stage not in COLLECTABLE_TRACKING_STAGES:
        return False, 'Order must be out for delivery before collecting card payment.'
    ensure_zoho_books_invoice_for_order(order)
    order.refresh_from_db()
    if not (order.zoho_books_invoice_id or '').strip():
        return (
            False,
            'No Zoho Books invoice for this order. '
            'Staff must create the invoice in Zoho Books before card collection.',
        )
    return True, ''


def _normalize_invoice_number(value: str) -> str:
    return (value or '').strip().upper()


def _invoice_number_variants(value: str) -> set[str]:
    """Accept common human-entry variants (e.g. NV- vs INV-)."""
    norm = _normalize_invoice_number(value)
    variants = {norm}
    if norm.startswith('NV-'):
        variants.add(f'INV-{norm[3:]}')
    if norm.startswith('INV-'):
        variants.add(f'NV-{norm[4:]}')
    return variants


def resolve_zoho_books_invoice_id(order: Order, raw_ref: str) -> tuple[str, str]:
    """
    Resolve Zoho Books invoice_id from numeric id, invoice number (INV-000030), or order link.
    """
    raw = (raw_ref or '').strip()
    linked_id = (order.zoho_books_invoice_id or '').strip()
    linked_number = (order.zoho_books_invoice_number or '').strip()

    if not raw:
        if linked_id:
            return linked_id, ''
        return '', 'invoice_id is required.'

    if linked_id and raw == linked_id:
        return linked_id, ''

    if linked_number and _normalize_invoice_number(raw) in _invoice_number_variants(linked_number):
        return linked_id, ''

    salesorder_id = (order.zoho_books_salesorder_id or '').strip()
    if not salesorder_id:
        return '', 'Order has no Zoho Books sales order.'

    if raw.isdigit():
        try:
            invoice = books_get_invoice(raw, store=order.store)
            if invoice_belongs_to_sales_order(invoice, salesorder_id):
                return str(invoice.get('invoice_id') or raw).strip(), ''
        except ZohoBooksError:
            pass

    try:
        for invoice in books_list_invoices_for_sales_order(salesorder_id, store=order.store):
            number = _normalize_invoice_number(str(invoice.get('invoice_number') or ''))
            invoice_id = str(invoice.get('invoice_id') or '').strip()
            if invoice_id == raw or number in _invoice_number_variants(raw):
                return invoice_id, ''
    except ZohoBooksError as exc:
        return '', str(exc)

    try:
        invoice = books_get_invoice(raw, store=order.store)
    except ZohoBooksError as exc:
        hint = ''
        if linked_id:
            number_hint = f' or {linked_number}' if linked_number else ''
            hint = f' Use invoice_id {linked_id}{number_hint} from the order.'
        return '', f'{exc}{hint}'

    if not invoice_belongs_to_sales_order(invoice, salesorder_id):
        return '', 'invoice_id does not belong to this order sales order.'
    return str(invoice.get('invoice_id') or raw).strip(), ''


def _validate_and_link_invoice(order: Order, invoice_id: str) -> tuple[bool, str]:
    invoice_id, err = resolve_zoho_books_invoice_id(order, invoice_id)
    if err:
        return False, err

    salesorder_id = (order.zoho_books_salesorder_id or '').strip()
    if not salesorder_id:
        return False, 'Order has no Zoho Books sales order.'

    try:
        invoice = books_get_invoice(invoice_id, store=order.store)
    except ZohoBooksError as exc:
        return False, str(exc)

    if not invoice_belongs_to_sales_order(invoice, salesorder_id):
        return False, 'invoice_id does not belong to this order sales order.'

    linked_id = (order.zoho_books_invoice_id or '').strip()
    if linked_id and linked_id != invoice_id:
        invoice_number = (order.zoho_books_invoice_number or linked_id).strip()
        return False, (
            f'invoice_id does not match the invoice already linked to this order ({invoice_number}).'
        )

    if not linked_id:
        order.zoho_books_invoice_id = invoice_id[:64]
        order.zoho_books_invoice_number = str(invoice.get('invoice_number') or '')[:64]
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

    return True, ''


def record_card_on_delivery_geidea_payment(
    order: Order,
    amount: Decimal,
    *,
    gateway_reference: str,
) -> Order:
    """Mark card-on-delivery order paid after Geidea success (no account credit)."""
    if not is_card_on_delivery_order(order):
        raise ValueError('Order is not card on delivery.')

    pay_amount = Decimal(str(amount)).quantize(Decimal('0.01'))
    if pay_amount <= 0:
        raise ValueError('Payment amount must be greater than zero.')

    if order.payment_status == Order.PaymentStatus.PAID:
        return order

    order.payment_status = Order.PaymentStatus.PAID
    order.gateway_reference = (gateway_reference or '')[:255]
    order.save(
        update_fields=['payment_status', 'gateway_reference', 'updated_at'],
    )
    logger.info(
        'card-on-delivery: Geidea payment recorded order=%s ref=%s amount=%s',
        order.pk,
        gateway_reference,
        pay_amount,
    )
    return order


def finalize_card_on_delivery_after_geidea(order_id: int) -> list[str]:
    """
    After Geidea payment for card on delivery:
    mark delivered, send tracking notifications, record Zoho invoice payment, journals.
    """
    steps: list[str] = []

    changed, deliver_msg = maybe_auto_mark_delivered_on_payment(order_id)
    if changed:
        steps.append(deliver_msg)

    gateway_reference = (
        Order.objects.filter(pk=order_id)
        .values_list('gateway_reference', flat=True)
        .first()
        or ''
    )
    ok, zoho_msg = staff_record_zoho_books_payment_for_order(
        order_id,
        payment_method=Order.PaymentMethod.CARD_ON_DELIVERY.value,
        gateway_reference=gateway_reference,
    )
    if ok:
        steps.append(zoho_msg)
        try:
            from shop.services.zoho_books_journals import create_payment_journals_for_order

            order = Order.objects.select_related('store').get(pk=order_id)
            create_payment_journals_for_order(order, order.payment_method)
            steps.append('Payment journals created.')
        except Exception as exc:
            logger.exception(
                'card-on-delivery: journal creation failed order=%s error=%s',
                order_id,
                exc,
            )
            steps.append(f'Journal creation pending: {exc}')
    else:
        steps.append(f'Zoho payment pending: {zoho_msg}')
        logger.warning(
            'card-on-delivery: Zoho payment failed after Geidea order=%s (%s)',
            order_id,
            zoho_msg,
        )

    return steps


def submit_card_on_delivery_collection(
    order_id: int,
    *,
    invoice_id: str,
    gateway_reference: str,
    amount: Decimal,
    transaction_status: str,
) -> tuple[bool, list[str]]:
    """
    Delivery workflow: verify Geidea payment, link invoice, mark delivered + paid + Zoho payment.

    Returns (success, steps).
    """
    steps: list[str] = []

    if not _transaction_status_successful(transaction_status):
        return False, ['transaction_status must indicate a successful payment (e.g. paid, success).']

    order = Order.objects.select_related('user', 'store').get(pk=order_id)

    if not is_card_on_delivery_order(order):
        return False, ['Order is not card on delivery.']
    if order.status == Order.Status.CANCELLED:
        return False, ['Order is cancelled.']

    if order.payment_status == Order.PaymentStatus.PAID:
        if (order.zoho_books_payment_id or '').strip():
            steps.append('Order already delivered and paid.')
            return True, steps
        steps.append('Payment already recorded locally; completing Zoho payment and delivery.')
    else:
        ready, reason = order_ready_for_card_on_delivery_collect(order)
        if not ready:
            return False, [reason]

    ok_invoice, invoice_msg = _validate_and_link_invoice(order, invoice_id)
    if not ok_invoice:
        return False, [invoice_msg]
    order.refresh_from_db()
    steps.append(f'Invoice linked: {order.zoho_books_invoice_number or invoice_id}.')

    pay_amount = Decimal(str(amount)).quantize(Decimal('0.01'))
    order_total = Decimal(str(order.total or 0)).quantize(Decimal('0.01'))
    if pay_amount != order_total:
        return False, [f'Payment amount mismatch. Expected {order_total}, received {pay_amount}.']

    geidea_entry, geidea_err = find_geidea_paid_entry_for_order(
        order,
        gateway_reference=gateway_reference,
    )
    if geidea_entry is None:
        return False, [geidea_err]

    geidea_order_id = str(geidea_entry.get('orderId') or gateway_reference or '').strip()
    geidea_amount = Decimal(str(geidea_entry.get('totalAmount', 0))).quantize(Decimal('0.01'))
    steps.append(f'Geidea payment verified (ref {geidea_order_id}, amount {geidea_amount}).')

    try:
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order_id)
            if locked.payment_status != Order.PaymentStatus.PAID:
                record_card_on_delivery_geidea_payment(
                    locked,
                    geidea_amount,
                    gateway_reference=geidea_order_id,
                )
                steps.append('Local payment marked paid.')
    except Exception as exc:
        logger.exception('card-on-delivery: payment record failed order=%s', order_id)
        return False, [str(exc)]

    finalize_steps = finalize_card_on_delivery_after_geidea(order_id)
    steps.extend(finalize_steps)

    order = Order.objects.get(pk=order_id)
    if order.payment_status != Order.PaymentStatus.PAID:
        return False, steps + ['Payment status was not updated.']
    if order.customer_tracking_stage != Order.CustomerTrackingStage.DELIVERED:
        return False, steps + ['Order was not marked delivered.']
    if not (order.zoho_books_payment_id or '').strip():
        return False, steps + ['Zoho Books payment was not recorded.']

    return True, steps
