"""Auto-link pay-on-delivery orders: delivered ↔ Zoho invoice payment."""

from __future__ import annotations

import logging

from shop.models import Order
from shop.services.order_email import handle_customer_tracking_stage_change
from shop.services.order_status_notifications import notify_order_tracking_status_change
from shop.services.order_tracking import record_tracking_stage
from shop.services.zoho_books_invoice import ensure_zoho_books_invoice_for_order
from shop.services.zoho_books_payment import (
    is_pay_on_delivery_payment_method,
    staff_record_zoho_books_payment_for_order,
)

logger = logging.getLogger(__name__)


def is_cod_order(order: Order) -> bool:
    return order.payment_method == Order.PaymentMethod.CASH_ON_DELIVERY


def order_ready_for_cod_collect(order: Order) -> tuple[bool, str]:
    if not is_cod_order(order):
        return False, 'Order is not cash on delivery.'
    if order.status == Order.Status.CANCELLED:
        return False, 'Order is cancelled.'
    if order.customer_tracking_stage == Order.CustomerTrackingStage.DELIVERED:
        if (order.zoho_books_payment_id or '').strip():
            return False, 'Order already delivered and paid.'
    ok, message = ensure_zoho_books_invoice_for_order(order)
    order.refresh_from_db()
    if not ok or not (order.zoho_books_invoice_id or '').strip():
        return (
            False,
            message
            if not ok
            else (
                'No Zoho Books invoice for this order. '
                'Staff must confirm the sales order and create the invoice in Zoho Books.'
            ),
        )
    if (order.zoho_books_payment_id or '').strip():
        return False, 'Zoho Books payment already recorded.'
    return True, ''


def _mark_cod_payment_collected(order: Order) -> None:
    if order.payment_status != Order.PaymentStatus.PAID:
        order.payment_status = Order.PaymentStatus.PAID
        order.save(update_fields=['payment_status', 'updated_at'])


def maybe_auto_record_payment_on_delivered(order: Order) -> str | None:
    """
    When a COD order is marked delivered, record Zoho Books payment.
    Card on delivery uses Geidea at the door — not manual Zoho on delivered.
    Returns a status message if attempted, else None.
    """
    if order.payment_method == Order.PaymentMethod.CARD_ON_DELIVERY:
        if order.payment_status == Order.PaymentStatus.PENDING:
            return (
                'Cannot mark delivered — card payment pending. '
                'Collect payment via Geidea first.'
            )
        return None
    if not is_cod_order(order):
        return None
    if (order.zoho_books_payment_id or '').strip():
        _mark_cod_payment_collected(order)
        return 'Zoho Books payment already recorded.'
    ensure_zoho_books_invoice_for_order(order)
    order.refresh_from_db()
    if not (order.zoho_books_invoice_id or '').strip():
        return (
            'Delivered, but no Zoho Books invoice — '
            'staff must create the invoice in Zoho Books first.'
        )

    ok, message = staff_record_zoho_books_payment_for_order(order.pk)
    if ok:
        order.refresh_from_db()
        _mark_cod_payment_collected(order)
        return message
    logger.warning(
        'auto-payment-on-delivered failed order=%s: %s',
        order.pk,
        message,
    )
    return f'Delivered, but Zoho payment failed: {message}'


def finalize_cod_delivery_and_payment(order_id: int) -> tuple[bool, list[str]]:
    """
    Delivery boy: mark COD order delivered, record cash collected, Zoho invoice paid.
    Returns (success, steps).
    """
    steps: list[str] = []
    order = Order.objects.select_related('user', 'store').get(pk=order_id)

    had_invoice = bool((order.zoho_books_invoice_id or '').strip())
    ready, reason = order_ready_for_cod_collect(order)
    if not ready:
        return False, [reason]

    order.refresh_from_db()
    if not had_invoice and (order.zoho_books_invoice_id or '').strip():
        steps.append(
            f'Linked Zoho Books invoice {order.zoho_books_invoice_number or order.zoho_books_invoice_id}.',
        )

    if order.customer_tracking_stage != Order.CustomerTrackingStage.DELIVERED:
        changed, deliver_msg = maybe_auto_mark_delivered_on_payment(order_id)
        if changed:
            steps.append(deliver_msg)

    order = Order.objects.get(pk=order_id)
    pay_msg = maybe_auto_record_payment_on_delivered(order)
    if pay_msg:
        steps.append(pay_msg)

    order = Order.objects.get(pk=order_id)
    if not (order.zoho_books_payment_id or '').strip():
        return False, steps

    if order.payment_status != Order.PaymentStatus.PAID:
        return False, steps + ['Cash collected but payment status was not updated.']

    return True, steps


def maybe_auto_mark_delivered_on_payment(order_id: int) -> tuple[bool, str]:
    """
    When pay-on-delivery payment is recorded, mark the order as delivered.
    Returns (changed, message).
    """
    order = Order.objects.select_related('user', 'store').get(pk=order_id)
    if not is_pay_on_delivery_payment_method(order.payment_method):
        return False, 'Not a pay-on-delivery order.'
    if order.status == Order.Status.CANCELLED:
        return False, 'Order is cancelled.'
    if order.customer_tracking_stage == Order.CustomerTrackingStage.DELIVERED:
        return False, 'Order already delivered.'

    previous_stage = order.customer_tracking_stage
    update_fields = ['customer_tracking_stage', 'tracking_stage_history', 'updated_at']
    order.customer_tracking_stage = Order.CustomerTrackingStage.DELIVERED
    record_tracking_stage(order, 'delivered', save=False)

    if order.status in (Order.Status.PENDING_ZOHO_SYNC, Order.Status.SYNC_FAILED):
        order.status = Order.Status.SYNCED
        update_fields.append('status')

    order.save(update_fields=update_fields)
    handle_customer_tracking_stage_change(order, previous_stage)
    notify_order_tracking_status_change(
        order,
        stage_key='delivered',
        previous_stage=previous_stage,
    )
    return True, 'Order marked delivered.'
