from __future__ import annotations

import logging
from decimal import Decimal

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from admin_dashboard.consumers import ADMIN_DASHBOARD_GROUP

logger = logging.getLogger(__name__)

DASHBOARD_SUMMARY_REFRESH = ['dashboard.summary']
ORDERS_LIST_REFRESH = ['dashboard.summary', 'orders.list']
RETURNS_LIST_REFRESH = ['dashboard.summary', 'returns.list']
CUSTOMERS_LIST_REFRESH = ['dashboard.summary', 'customers.list']


def _quantize_decimal(value) -> str:
    return str(Decimal(str(value or 0)).quantize(Decimal('0.01')))


def _order_code(order) -> str:
    from shop.serializers import order_code_for_order

    return order_code_for_order(order)


def _tracking_label(stage_key: str) -> str:
    from shop.serializers import ORDER_CUSTOMER_TRACKING_STAGE_LABELS

    key = (stage_key or '').strip()
    if not key:
        return 'Unknown'
    return ORDER_CUSTOMER_TRACKING_STAGE_LABELS.get(
        key,
        key.replace('_', ' ').title(),
    )


def _status_label(order, status: str) -> str:
    for value, label in order.Status.choices:
        if value == status:
            return label
    return (status or '').replace('_', ' ').title()


def broadcast_admin_dashboard_event(payload: dict) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    try:
        async_to_sync(channel_layer.group_send)(
            ADMIN_DASHBOARD_GROUP,
            {
                'type': 'admin_dashboard_event',
                'payload': payload,
            },
        )
    except Exception:
        logger.warning('Failed to broadcast admin dashboard event.', exc_info=True)


def _order_event_base(order) -> dict:
    return {
        'order_id': order.pk,
        'order_code': _order_code(order),
        'total': _quantize_decimal(order.total),
        'currency': order.currency or 'AED',
    }


def broadcast_order_created(order) -> None:
    code = _order_code(order)
    broadcast_admin_dashboard_event(
        {
            'event': 'order.created',
            **_order_event_base(order),
            'message': f'New order {code} received.',
            'refresh': ORDERS_LIST_REFRESH,
        }
    )


def broadcast_order_cancelled(order) -> None:
    code = _order_code(order)
    broadcast_admin_dashboard_event(
        {
            'event': 'order.cancelled',
            **_order_event_base(order),
            'status': order.status,
            'message': f'Order {code} was cancelled.',
            'refresh': ORDERS_LIST_REFRESH,
        }
    )


def broadcast_order_status_updated(order, *, previous_status: str) -> None:
    code = _order_code(order)
    broadcast_admin_dashboard_event(
        {
            'event': 'order.status_updated',
            **_order_event_base(order),
            'previous_status': previous_status,
            'status': order.status,
            'status_label': _status_label(order, order.status),
            'message': (
                f'Order {code} status updated to {_status_label(order, order.status)}.'
            ),
            'refresh': ORDERS_LIST_REFRESH,
        }
    )


def broadcast_order_tracking_updated(order, *, previous_tracking_stage: str) -> None:
    code = _order_code(order)
    stage = order.customer_tracking_stage or ''
    broadcast_admin_dashboard_event(
        {
            'event': 'order.tracking_updated',
            **_order_event_base(order),
            'previous_tracking_stage': previous_tracking_stage,
            'tracking_stage': stage,
            'tracking_stage_label': _tracking_label(stage),
            'message': f'Order {code} is now {_tracking_label(stage)}.',
            'refresh': ORDERS_LIST_REFRESH,
        }
    )


def broadcast_order_paid(order) -> None:
    code = _order_code(order)
    broadcast_admin_dashboard_event(
        {
            'event': 'order.paid',
            **_order_event_base(order),
            'payment_method': order.payment_method or '',
            'payment_status': order.payment_status or '',
            'gateway_reference': (order.gateway_reference or '').strip(),
            'message': f'Payment received for order {code}.',
            'refresh': ORDERS_LIST_REFRESH,
        }
    )


def _customer_display_name(user) -> str:
    parts = [user.first_name or '', user.last_name or '']
    return ' '.join(part for part in parts if part).strip() or user.email


def broadcast_customer_registered(user) -> None:
    name = _customer_display_name(user)
    broadcast_admin_dashboard_event(
        {
            'event': 'customer.registered',
            'customer_id': user.pk,
            'customer_email': user.email,
            'customer_name': name,
            'message': f'New customer registered: {user.email}.',
            'refresh': CUSTOMERS_LIST_REFRESH,
        }
    )


def _return_event_base(order_return) -> dict:
    order = order_return.order
    return {
        'return_id': order_return.pk,
        'order_id': order.pk,
        'order_code': _order_code(order),
        'status': order_return.status,
        'return_reason': order_return.return_reason,
    }


def broadcast_return_created(order_return) -> None:
    code = _order_code(order_return.order)
    broadcast_admin_dashboard_event(
        {
            'event': 'return.created',
            **_return_event_base(order_return),
            'message': f'Return request submitted for order {code}.',
            'refresh': RETURNS_LIST_REFRESH,
        }
    )


def broadcast_return_status_updated(order_return, *, previous_status: str) -> None:
    code = _order_code(order_return.order)
    status_label = dict(order_return.Status.choices).get(
        order_return.status,
        (order_return.status or '').replace('_', ' ').title(),
    )
    broadcast_admin_dashboard_event(
        {
            'event': 'return.status_updated',
            **_return_event_base(order_return),
            'previous_status': previous_status,
            'status_label': status_label,
            'message': (
                f'Return #{order_return.pk} for order {code} updated to {status_label}.'
            ),
            'refresh': RETURNS_LIST_REFRESH,
        }
    )
