"""Record Zoho Books customer payments against order invoices."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone as dj_tz

from shop.models import Order
from shop.serializers import order_code_for_order
from shop.services.zoho_books import (
    ZohoBooksError,
    books_create_customer_payment,
    books_get_invoice,
    invoice_belongs_to_sales_order,
    zoho_books_enabled,
    store_has_books_config,
)
from shop.services.zoho_books_invoice import _resolve_customer_id

logger = logging.getLogger(__name__)

# Zoho Books customerpayments payment_mode values.
BOOKS_PAYMENT_MODE_BY_METHOD = {
    Order.PaymentMethod.CASH_ON_DELIVERY: 'cash',
    Order.PaymentMethod.CARD_ON_DELIVERY: 'creditcard',
    Order.PaymentMethod.PAYMENT_GATEWAY: 'creditcard',
    Order.PaymentMethod.PAY_BY_LINK: 'banktransfer',
}

# Prepaid at checkout: customer payment recorded after invoice → Zoho invoice status paid.
PREPAID_AT_CHECKOUT_PAYMENT_METHODS = frozenset({
    Order.PaymentMethod.PAY_BY_LINK.value,
    Order.PaymentMethod.PAYMENT_GATEWAY.value,
})


def is_prepaid_at_checkout_payment_method(payment_method: str) -> bool:
    return (payment_method or '').strip() in PREPAID_AT_CHECKOUT_PAYMENT_METHODS


PAY_ON_DELIVERY_PAYMENT_METHODS = frozenset({
    Order.PaymentMethod.CASH_ON_DELIVERY.value,
    Order.PaymentMethod.CARD_ON_DELIVERY.value,
})


def is_pay_on_delivery_payment_method(payment_method: str) -> bool:
    return (payment_method or '').strip() in PAY_ON_DELIVERY_PAYMENT_METHODS


def books_payment_mode_for_order(order: Order, *, override: str = '') -> str:
    raw = (override or order.payment_method or '').strip()
    for choice in Order.PaymentMethod:
        if choice.value == raw:
            return BOOKS_PAYMENT_MODE_BY_METHOD.get(choice, 'others')
    return 'others'


def order_ready_for_books_payment(order: Order) -> tuple[bool, str]:
    if not zoho_books_enabled():
        return False, 'Zoho Books invoice creation is disabled.'
    if not store_has_books_config(order.store):
        return False, 'Store is missing Zoho Books org configuration.'
    if order.status == Order.Status.CANCELLED:
        return False, 'Cancelled orders cannot be paid.'
    if not (order.zoho_books_invoice_id or '').strip():
        return False, 'Order has no Zoho Books invoice yet.'
    if (order.zoho_books_payment_id or '').strip():
        return False, 'Payment was already recorded for this order.'
    return True, ''


def record_zoho_books_payment_for_order(
    order: Order,
    *,
    amount: Decimal | None = None,
    payment_method: str = '',
    gateway_reference: str = '',
    paid_at=None,
) -> Order:
    """
    Create a Zoho Books customer payment applied to the order invoice.
    Raises ZohoBooksError on API failure.
    """
    order = Order.objects.select_related('user', 'store').get(pk=order.pk)
    ready, reason = order_ready_for_books_payment(order)
    if not ready:
        raise ZohoBooksError(reason)

    invoice_id = (order.zoho_books_invoice_id or '').strip()
    invoice = books_get_invoice(invoice_id, store=order.store)
    salesorder_id = (order.zoho_books_salesorder_id or '').strip()
    if salesorder_id and not invoice_belongs_to_sales_order(invoice, salesorder_id):
        raise ZohoBooksError(
            'Linked invoice does not belong to this order sales order. '
            'Create the correct invoice in Zoho Books and retry collect-cod.',
        )

    balance_due = Decimal(str(invoice.get('balance', 0))).quantize(Decimal('0.01'))
    if balance_due <= 0:
        raise ZohoBooksError(
            'Invoice has no balance due. It may already be paid or the wrong invoice is linked.',
        )

    pay_amount = amount if amount is not None else Decimal(str(order.total or 0))
    pay_amount = pay_amount.quantize(Decimal('0.01'))
    if pay_amount <= 0:
        raise ZohoBooksError('Payment amount must be greater than zero.')
    if pay_amount > balance_due:
        pay_amount = balance_due
    customer_id = _resolve_customer_id(order)
    payment_mode = books_payment_mode_for_order(order, override=payment_method)
    paid_dt = paid_at or dj_tz.now()
    if hasattr(paid_dt, 'date'):
        paid_date = paid_dt.date().isoformat()
    else:
        paid_date = dj_tz.now().date().isoformat()

    description_parts = [
        f'AoneGt order #{order.pk}',
        order.get_payment_method_display(),
    ]
    if gateway_reference:
        description_parts.append(f'ref {gateway_reference}')
    description = ' — '.join(description_parts)[:500]

    body = {
        'customer_id': customer_id,
        'payment_mode': payment_mode,
        'amount': float(pay_amount),
        'date': paid_date,
        'reference_number': order_code_for_order(order)[:100],
        'description': description,
        'invoices': [
            {
                'invoice_id': invoice_id,
                'amount_applied': float(pay_amount),
            },
        ],
    }

    payment = books_create_customer_payment(body, store=order.store)
    payment_id = str(payment.get('payment_id') or '').strip()
    if not payment_id:
        raise ZohoBooksError('Zoho Books payment_id missing in response.')

    order.zoho_books_payment_id = payment_id[:64]
    order.zoho_books_payment_error = ''
    order.zoho_books_paid_at = paid_dt if hasattr(paid_dt, 'isoformat') else dj_tz.now()
    order.save(
        update_fields=[
            'zoho_books_payment_id',
            'zoho_books_payment_error',
            'zoho_books_paid_at',
            'updated_at',
        ],
    )
    logger.info(
        'zoho-books: payment recorded order=%s payment_id=%s mode=%s amount=%s',
        order.pk,
        payment_id,
        payment_mode,
        pay_amount,
    )
    return order


def staff_record_zoho_books_payment_for_order(
    order_id: int,
    *,
    amount=None,
    payment_method: str = '',
    gateway_reference: str = '',
) -> tuple[bool, str]:
    """Staff-triggered payment recording against the order invoice."""
    if not zoho_books_enabled():
        return False, 'Zoho Books is disabled.'
    try:
        order = Order.objects.select_related('user', 'store').get(pk=order_id)
    except Order.DoesNotExist:
        return False, 'Order not found.'

    ready, reason = order_ready_for_books_payment(order)
    if not ready:
        return False, reason

    try:
        with transaction.atomic():
            locked = (
                Order.objects.select_for_update()
                .select_related('user', 'store')
                .get(pk=order_id)
            )
            record_zoho_books_payment_for_order(
                locked,
                amount=amount,
                payment_method=payment_method,
                gateway_reference=gateway_reference,
            )
    except ZohoBooksError as exc:
        logger.exception('zoho-books: staff payment failed order=%s (%s)', order_id, exc)
        Order.objects.filter(pk=order_id).update(
            zoho_books_payment_error=str(exc)[:5000],
            updated_at=dj_tz.now(),
        )
        return False, str(exc)
    except Exception as exc:
        logger.exception('zoho-books: staff payment unexpected error order=%s', order_id)
        Order.objects.filter(pk=order_id).update(
            zoho_books_payment_error=str(exc)[:5000],
            updated_at=dj_tz.now(),
        )
        return False, str(exc)

    return True, 'Zoho Books payment recorded.'


def maybe_record_zoho_books_payment_for_order(
    order_id: int,
    *,
    gateway_reference: str = '',
) -> None:
    """
    Best-effort Zoho Books payment recording; never raises (checkout / API safe).
    """
    try:
        order = Order.objects.select_related('user', 'store').get(pk=order_id)
    except Order.DoesNotExist:
        return

    ready, reason = order_ready_for_books_payment(order)
    if not ready:
        if reason != 'Payment was already recorded for this order.':
            logger.info(
                'zoho-books: skip payment order=%s (%s)',
                order_id,
                reason,
            )
        return

    try:
        with transaction.atomic():
            locked = (
                Order.objects.select_for_update()
                .select_related('user', 'store')
                .get(pk=order_id)
            )
            record_zoho_books_payment_for_order(
                locked,
                gateway_reference=gateway_reference,
            )
    except ZohoBooksError as exc:
        logger.exception('zoho-books: payment failed order=%s (%s)', order_id, exc)
        Order.objects.filter(pk=order_id).update(
            zoho_books_payment_error=str(exc)[:5000],
            updated_at=dj_tz.now(),
        )
    except Exception as exc:
        logger.exception('zoho-books: unexpected payment error order=%s', order_id)
        Order.objects.filter(pk=order_id).update(
            zoho_books_payment_error=str(exc)[:5000],
            updated_at=dj_tz.now(),
        )


def create_zoho_books_advance_payment_for_order(order) -> None:
    """
    Create a standalone advance payment received in Zoho Books for a prepaid order.
    This payment is NOT linked to any invoice - Zoho staff will apply it manually
    when they create the invoice against the confirmed sales order.
    Called automatically at checkout for pay_by_link and payment_gateway orders.
    """
    from shop.services.zoho_books import (
        ZohoBooksError,
        _books_request,
        store_has_books_config,
        zoho_books_enabled,
    )

    order = Order.objects.select_related('user', 'store').get(pk=order.pk)
    store = order.store

    if not zoho_books_enabled():
        logger.info("Zoho Books disabled, skipping advance payment for order %s", order.pk)
        return

    if not store_has_books_config(store):
        logger.info("Store has no Books config, skipping advance payment for order %s", order.pk)
        return

    if (order.zoho_books_payment_id or '').strip():
        logger.info("Advance payment already recorded for order %s, skipping", order.pk)
        return

    if not (order.zoho_books_salesorder_id or '').strip():
        logger.warning("No sales order id on order %s, skipping advance payment", order.pk)
        return

    try:
        customer_id = _resolve_customer_id(order)
    except Exception as exc:
        logger.error("Could not resolve customer id for order %s: %s", order.pk, exc, exc_info=True)
        return

    pay_amount = order.total
    if not pay_amount or pay_amount <= 0:
        logger.warning("Order %s has zero/negative total, skipping advance payment", order.pk)
        return

    gateway_reference = (order.gateway_reference or '').strip()
    payment_method = (order.payment_method or '').strip()

    payment_mode_map = {
        'pay_by_link': 'pay_by_link',
        'payment_gateway': 'credit_card',
    }
    payment_mode = payment_mode_map.get(payment_method, 'pay_by_link')

    so_number = (order.zoho_books_salesorder_number or '').strip()
    order_ref = so_number if so_number else f'#{order.pk}'
    description = f'AoneGT order {order_ref} - advance payment ({payment_method})'
    if gateway_reference:
        description += f' ref {gateway_reference}'

    body = {
        'customer_id': customer_id,
        'payment_mode': payment_mode,
        'amount': float(pay_amount),
        'date': order.created_at.date().isoformat(),
        'reference_number': order_code_for_order(order)[:100],
        'description': description,
    }

    try:
        payload = _books_request('POST', 'customerpayments', store=store, json_data=body)
        payment = payload.get('payment') or {}
        payment_id = str(payment.get('payment_id') or '').strip()
        if not payment_id:
            raise ZohoBooksError('Zoho Books did not return payment_id after creating advance payment.')

        order.__class__.objects.filter(pk=order.pk).update(
            zoho_books_payment_id=payment_id,
            zoho_books_paid_at=dj_tz.now(),
            zoho_books_payment_error='',
        )
        order.zoho_books_payment_id = payment_id
        logger.info("Advance payment %s created in Zoho Books for order %s", payment_id, order.pk)

    except ZohoBooksError as exc:
        logger.error("Zoho Books advance payment failed for order %s: %s", order.pk, exc, exc_info=True)
        order.__class__.objects.filter(pk=order.pk).update(
            zoho_books_payment_error=str(exc)[:500],
        )


def maybe_create_zoho_books_advance_payment_for_order(order) -> None:
    """
    Best-effort wrapper. Swallows all exceptions so checkout is never blocked.
    Only runs for pay_by_link and payment_gateway orders.
    """
    prepaid_methods = {'pay_by_link', 'payment_gateway'}
    if (order.payment_method or '') not in prepaid_methods:
        logger.info(
            "Order %s payment method %s is not prepaid, skipping advance payment",
            order.pk, order.payment_method,
        )
        return

    try:
        create_zoho_books_advance_payment_for_order(order)
    except Exception as exc:
        logger.error(
            "Unexpected error creating advance payment for order %s: %s",
            order.pk, exc,
            exc_info=True,
        )
