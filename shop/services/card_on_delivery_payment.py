"""Card on delivery: Geidea payment at door, then delivered + Zoho invoice paid."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction

from shop.models import Order
from shop.services.order_delivery_payment import maybe_auto_mark_delivered_on_payment
from shop.services.zoho_books_invoice import ensure_zoho_books_invoice_for_order
from shop.services.zoho_books_payment import staff_record_zoho_books_payment_for_order

logger = logging.getLogger(__name__)

COLLECTABLE_TRACKING_STAGES = frozenset({
    Order.CustomerTrackingStage.OUT_FOR_DELIVERY,
})


def is_card_on_delivery_order(order: Order) -> bool:
    return order.payment_method == Order.PaymentMethod.CARD_ON_DELIVERY


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
    After Geidea callback for card on delivery:
    mark delivered, send tracking notifications, record Zoho invoice payment.
    """
    steps: list[str] = []

    changed, deliver_msg = maybe_auto_mark_delivered_on_payment(order_id)
    if changed:
        steps.append(deliver_msg)

    ok, zoho_msg = staff_record_zoho_books_payment_for_order(
        order_id,
        payment_method=Order.PaymentMethod.CARD_ON_DELIVERY.value,
        gateway_reference=(
            Order.objects.filter(pk=order_id)
            .values_list('gateway_reference', flat=True)
            .first()
            or ''
        ),
    )
    if ok:
        steps.append(zoho_msg)
    else:
        steps.append(f'Zoho payment pending: {zoho_msg}')
        logger.warning(
            'card-on-delivery: Zoho payment failed after Geidea order=%s (%s)',
            order_id,
            zoho_msg,
        )

    return steps
