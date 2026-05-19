from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone as dj_tz

from shop.serializers import order_code_for_order

logger = logging.getLogger(__name__)


def order_confirmation_email_enabled() -> bool:
    return getattr(settings, 'ORDER_CONFIRMATION_EMAIL', True)


def order_out_for_delivery_email_enabled() -> bool:
    return getattr(settings, 'ORDER_OUT_FOR_DELIVERY_EMAIL', True)


def send_order_placed_email(order, user) -> bool:
    """
    Send a plain-text order confirmation to the customer.
    Returns True if sent; False if skipped or failed (checkout is not aborted).
    """
    if not order_confirmation_email_enabled():
        return False

    to_email = (getattr(user, 'email', None) or '').strip().lower()
    if not to_email or '@' not in to_email:
        logger.warning('order-email: skip order=%s — user has no valid email', order.pk)
        return False

    code = order_code_for_order(order)
    greeting = (getattr(user, 'first_name', None) or '').strip() or 'there'
    currency = (order.currency or 'AED').strip() or 'AED'
    payment_label = order.get_payment_method_display()

    lines = []
    for item in order.items.all():
        line_total = item.line_total
        lines.append(
            f'  - {item.product_name} × {item.quantity} — {currency} {line_total}',
        )
    items_block = '\n'.join(lines) if lines else '  (no line items)'

    address_parts = [
        order.shipping_name,
        order.shipping_phone,
        order.shipping_address,
        order.shipping_city,
        order.shipping_state,
        order.shipping_postal_code,
        order.shipping_country,
    ]
    address_block = '\n'.join(p for p in address_parts if p)

    subject = f'AoneGt order confirmation #{code}'
    message = (
        f'Hello {greeting},\n\n'
        f'Thank you for your order. We have received it and will process it shortly.\n\n'
        f'Order: #{code}\n'
        f'Store order ID: {order.pk}\n'
        f'Payment: {payment_label}\n\n'
        f'Items:\n{items_block}\n\n'
        f'Subtotal: {currency} {order.subtotal}\n'
        f'VAT ({order.vat_percent}%): {currency} {order.vat_amount}\n'
        f'Shipping: {currency} {order.shipping_amount}\n'
    )
    if order.loyalty_discount and order.loyalty_discount > 0:
        message += f'Loyalty discount: -{currency} {order.loyalty_discount}\n'
    message += (
        f'Total: {currency} {order.total}\n\n'
        f'Delivery address:\n{address_block}\n\n'
        f'You can track your order in the AoneGt app.\n\n'
        f'Thank you for shopping with us.'
    )

    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [to_email],
            fail_silently=False,
        )
        return True
    except Exception as exc:
        logger.exception(
            'order-email: failed order=%s to=%s (%s: %s)',
            order.pk,
            to_email,
            type(exc).__name__,
            exc,
        )
        return False


def send_order_out_for_delivery_email(order, user=None) -> bool:
    """
    Notify the customer that their order is out for delivery.
    Idempotent: skips if already sent (out_for_delivery_email_sent_at set).
    """
    if not order_out_for_delivery_email_enabled():
        return False

    if getattr(order, 'out_for_delivery_email_sent_at', None):
        return False

    user = user or getattr(order, 'user', None)
    if user is None:
        logger.warning('order-email: skip out-for-delivery order=%s — no user', order.pk)
        return False

    to_email = (getattr(user, 'email', None) or '').strip().lower()
    if not to_email or '@' not in to_email:
        logger.warning(
            'order-email: skip out-for-delivery order=%s — user has no valid email',
            order.pk,
        )
        return False

    code = order_code_for_order(order)
    greeting = (getattr(user, 'first_name', None) or '').strip() or 'there'
    currency = (order.currency or 'AED').strip() or 'AED'

    address_parts = [
        order.shipping_name,
        order.shipping_phone,
        order.shipping_address,
        order.shipping_city,
        order.shipping_state,
        order.shipping_postal_code,
        order.shipping_country,
    ]
    address_block = '\n'.join(p for p in address_parts if p)

    subject = f'AoneGt order #{code} — out for delivery'
    message = (
        f'Hello {greeting},\n\n'
        f'Good news — your order #{code} is out for delivery and should reach you soon.\n\n'
        f'Delivery address:\n{address_block}\n\n'
        f'Order total: {currency} {order.total}\n\n'
        f'You can track your order in the AoneGt app.\n\n'
        f'Thank you for shopping with us.'
    )

    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [to_email],
            fail_silently=False,
        )
        from shop.models import Order

        Order.objects.filter(pk=order.pk).update(
            out_for_delivery_email_sent_at=dj_tz.now(),
        )
        order.out_for_delivery_email_sent_at = dj_tz.now()
        return True
    except Exception as exc:
        logger.exception(
            'order-email: out-for-delivery failed order=%s to=%s (%s: %s)',
            order.pk,
            to_email,
            type(exc).__name__,
            exc,
        )
        return False


def handle_customer_tracking_stage_change(order, previous_stage: Optional[str] = None) -> None:
    """Send transactional emails when customer_tracking_stage changes."""
    new_stage = (getattr(order, 'customer_tracking_stage', '') or '').strip()
    prev = (previous_stage or '').strip()
    if new_stage == 'out_for_delivery' and prev != 'out_for_delivery':
        send_order_out_for_delivery_email(order)
