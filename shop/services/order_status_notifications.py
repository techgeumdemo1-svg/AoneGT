"""In-app + FCM notifications when customer order tracking status changes."""

from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings

from shop.models import FCMDeviceToken, UserNotification
from shop.services.notifications import create_user_notification
from shop.services.push_notifications import send_push_notification

logger = logging.getLogger(__name__)

ORDER_STATUS_UPDATED_EVENT = 'order_status_updated'

_STAGE_DISPLAY_LABELS = {
    'pending': 'Pending',
    'packed': 'Packed',
    'out_for_delivery': 'Out for Delivery',
    'delivered': 'Delivered',
    'returned': 'Returned',
    'cancelled': 'Cancelled',
}

_STAGE_BODY_TEMPLATES = {
    'pending': 'Your order #{code} is pending.',
    'packed': 'Your order #{code} has been packed.',
    'out_for_delivery': 'Your order #{code} is out for delivery.',
    'delivered': 'Your order #{code} was delivered.',
    'returned': 'Your order #{code} was marked as returned.',
    'cancelled': 'Your order #{code} was cancelled.',
}


def _normalized_stage(stage: Optional[str]) -> str:
    key = (stage or '').strip().lower()
    if key == 'confirmed':
        return 'packed'
    return key


def _stage_display_label(stage_key: str) -> str:
    return _STAGE_DISPLAY_LABELS.get(
        stage_key,
        stage_key.replace('_', ' ').title(),
    )


def _notification_copy(order, stage_key: str) -> tuple[str, str]:
    from shop.serializers import order_code_for_order

    code = order_code_for_order(order)
    display_status = _stage_display_label(stage_key)
    template = _STAGE_BODY_TEMPLATES.get(stage_key, 'Your order #{code} status was updated.')
    body = template.format(code=code)
    title = f'Order #{code}: {display_status}'
    return title, body


def notify_order_tracking_status_change(
    order,
    *,
    stage_key: str,
    previous_stage: Optional[str] = None,
    display_status: Optional[str] = None,
) -> None:
    """
    Create in-app notification and send FCM push to the order owner.
    Skips when the normalized stage did not change (duplicate admin PATCH).
    """
    new_stage = _normalized_stage(stage_key)
    if not new_stage:
        return

    prev_stage = _normalized_stage(previous_stage)
    if new_stage == prev_stage and new_stage != 'cancelled':
        return

    user = getattr(order, 'user', None)
    if not user or not getattr(user, 'is_active', True):
        return

    from shop.serializers import order_code_for_order

    label = display_status or _stage_display_label(new_stage)
    title, body = _notification_copy(order, new_stage)
    code = order_code_for_order(order)
    payload = {
        'event': ORDER_STATUS_UPDATED_EVENT,
        'order_id': order.pk,
        'store_id': order.store_id,
        'order_code': code,
        'status': new_stage,
        'display_status': label,
    }

    try:
        create_user_notification(
            user,
            UserNotification.Kind.ORDER,
            title=title,
            body=body,
            payload=payload,
        )
    except Exception:
        logger.exception(
            'Failed creating order status notification for order=%s stage=%s',
            order.pk,
            new_stage,
        )

    if not getattr(settings, 'ORDER_TRACKING_PUSH_ENABLED', True):
        return

    try:
        tokens = list(
            FCMDeviceToken.objects.filter(
                user=user,
                is_active=True,
                push_enabled=True,
            ).values_list('token', flat=True),
        )
        if not tokens:
            return

        send_push_notification(
            tokens=tokens,
            title=title,
            body=body,
            data={
                'type': ORDER_STATUS_UPDATED_EVENT,
                'event': ORDER_STATUS_UPDATED_EVENT,
                'order_id': str(order.pk),
                'store_id': str(order.store_id),
                'order_code': code,
                'status': new_stage,
                'display_status': label,
                'click_action': 'OPEN_ORDER',
            },
        )
    except Exception:
        logger.exception(
            'Failed sending order status push for order=%s stage=%s',
            order.pk,
            new_stage,
        )
