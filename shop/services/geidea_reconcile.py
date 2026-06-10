"""Reconcile Geidea payments when the callback was missed."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction

from shop.models import Order
from shop.services.account_credit import credit_user_for_prepaid_order
from shop.services.card_on_delivery_payment import (
    finalize_card_on_delivery_after_geidea,
    is_card_on_delivery_order,
    record_card_on_delivery_geidea_payment,
)
from shop.services.geidea import fetch_geidea_orders_by_merchant_ref
from shop.services.zoho_books_payment import maybe_create_zoho_books_advance_payment_for_order

logger = logging.getLogger(__name__)


def _paid_geidea_entry(orders_list: list) -> dict | None:
    return next(
        (
            entry
            for entry in orders_list
            if entry.get('status') == 'Success' and entry.get('detailedStatus') == 'Paid'
        ),
        None,
    )


def reconcile_missed_geidea_callback(order: Order) -> tuple[str, list[str]]:
    """
    Fetch Geidea by merchant ref and apply payment if succeeded but callback missed.

    Returns (status, steps) where status is 'paid' or 'pending'.
    """
    if order.payment_status == Order.PaymentStatus.PAID:
        return 'paid', ['Order already paid.']

    merchant_ref = (order.geidea_merchant_ref or '').strip()
    if not merchant_ref:
        return 'pending', []

    orders_list = fetch_geidea_orders_by_merchant_ref(merchant_ref)
    paid_entry = _paid_geidea_entry(orders_list)
    if not paid_entry:
        return 'pending', []

    paid_amount = Decimal(str(paid_entry.get('totalAmount', 0)))
    if paid_amount != order.total:
        logger.error(
            'Geidea reconcile — amount mismatch order=%s expected=%s received=%s',
            order.pk,
            order.total,
            paid_amount,
        )
        return 'pending', []

    geidea_order_id = paid_entry.get('orderId', '')
    steps: list[str] = []

    logger.warning(
        'Geidea reconcile — missed callback order=%s geidea_order_id=%s',
        order.pk,
        geidea_order_id,
    )

    try:
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            if locked.payment_status == Order.PaymentStatus.PAID:
                return 'paid', ['Order already paid.']

            if is_card_on_delivery_order(locked):
                record_card_on_delivery_geidea_payment(
                    locked,
                    paid_amount,
                    gateway_reference=geidea_order_id,
                )
                steps.append('Card payment recorded.')
            else:
                credit_user_for_prepaid_order(
                    locked,
                    amount=paid_amount,
                    gateway_reference=geidea_order_id,
                )
                steps.append('Prepaid payment recorded.')
            order = locked
    except Exception as exc:
        logger.error(
            'Geidea reconcile — atomic block failed order=%s error=%s',
            order.pk,
            exc,
        )
        return 'pending', []

    try:
        if is_card_on_delivery_order(order):
            steps.extend(finalize_card_on_delivery_after_geidea(order.pk))
        else:
            maybe_create_zoho_books_advance_payment_for_order(order)
            steps.append('Zoho advance payment processed.')
    except Exception as exc:
        logger.critical(
            'Geidea reconcile — post-payment failed order=%s error=%s',
            order.pk,
            exc,
        )
        steps.append(f'Post-payment step failed: {exc}')

    return 'paid', steps
