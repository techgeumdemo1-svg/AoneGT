"""Prepaid AED account credit (gateway / pay-by-link payments, invoice settlement)."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction

from shop.models import AccountCreditLedger, Order
from shop.services.zoho_books_payment import is_prepaid_at_checkout_payment_method

logger = logging.getLogger(__name__)

User = get_user_model()


def _quantize(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal('0.01'))


def get_user_credit_balance(user) -> Decimal:
    return _quantize(getattr(user, 'credit_balance_aed', 0))


def _ledger_kind_for_payment_method(payment_method: str) -> str:
    if payment_method == Order.PaymentMethod.PAY_BY_LINK.value:
        return AccountCreditLedger.Kind.PAYLINK_PAYMENT
    return AccountCreditLedger.Kind.GATEWAY_PAYMENT


def credit_user_for_prepaid_order(
    order: Order,
    amount: Decimal | None = None,
    *,
    gateway_reference: str = '',
) -> Order:
    """
    Credit user account when gateway/pay-by-link payment succeeds.
    Idempotent if order is already paid.
    """
    if not is_prepaid_at_checkout_payment_method(order.payment_method):
        raise ValueError('Order payment method is not prepaid at checkout.')

    pay_amount = _quantize(amount if amount is not None else order.total)
    if pay_amount <= 0:
        raise ValueError('Payment amount must be greater than zero.')

    order = Order.objects.select_related('user').get(pk=order.pk)
    if order.payment_status == Order.PaymentStatus.PAID:
        return order

    user = User.objects.select_for_update().get(pk=order.user_id)
    new_balance = get_user_credit_balance(user) + pay_amount
    user.credit_balance_aed = new_balance
    user.save(update_fields=['credit_balance_aed'])

    AccountCreditLedger.objects.create(
        user=user,
        order=order,
        kind=_ledger_kind_for_payment_method(order.payment_method),
        amount=pay_amount,
        balance_after=new_balance,
        reference=(gateway_reference or '')[:255],
        note=f'Prepaid checkout order #{order.pk}',
    )

    order.payment_status = Order.PaymentStatus.PAID
    order.prepaid_credited_amount = pay_amount
    order.gateway_reference = (gateway_reference or order.gateway_reference or '')[:255]
    order.save(
        update_fields=[
            'payment_status',
            'prepaid_credited_amount',
            'gateway_reference',
            'updated_at',
        ],
    )
    logger.info(
        'account-credit: credited user=%s order=%s amount=%s balance=%s',
        user.pk,
        order.pk,
        pay_amount,
        new_balance,
    )
    return order


def apply_prepaid_credit_on_invoice(order: Order) -> tuple[Decimal, Decimal]:
    """
    Deduct invoice total from user credit for prepaid paid orders.
    Returns (amount_applied, remainder_left_on_account).

    If invoice total is less than prepaid credited amount, the difference
    remains on the user's credit balance automatically.
    """
    if not is_prepaid_at_checkout_payment_method(order.payment_method):
        return Decimal('0.00'), Decimal('0.00')
    if order.payment_status != Order.PaymentStatus.PAID:
        return Decimal('0.00'), Decimal('0.00')
    if _quantize(order.credit_applied_on_invoice) > 0:
        applied = _quantize(order.credit_applied_on_invoice)
        remainder = _quantize(order.credit_refunded_remainder)
        return applied, remainder

    invoice_total = _quantize(order.total)
    prepaid = _quantize(order.prepaid_credited_amount)
    amount_to_apply = min(invoice_total, prepaid, get_user_credit_balance(order.user))
    if amount_to_apply <= 0:
        return Decimal('0.00'), prepaid

    user = User.objects.select_for_update().get(pk=order.user_id)
    current = get_user_credit_balance(user)
    if current < amount_to_apply:
        raise ValueError('Insufficient account credit to settle this invoice.')

    new_balance = current - amount_to_apply
    user.credit_balance_aed = new_balance
    user.save(update_fields=['credit_balance_aed'])

    remainder = prepaid - amount_to_apply
    if remainder < 0:
        remainder = Decimal('0.00')

    AccountCreditLedger.objects.create(
        user=user,
        order=order,
        kind=AccountCreditLedger.Kind.INVOICE_APPLICATION,
        amount=-amount_to_apply,
        balance_after=new_balance,
        reference=(order.zoho_books_invoice_id or '')[:255],
        note=f'Invoice settlement order #{order.pk} (invoice total {invoice_total} AED)',
    )

    order.credit_applied_on_invoice = amount_to_apply
    order.credit_refunded_remainder = remainder
    order.save(
        update_fields=[
            'credit_applied_on_invoice',
            'credit_refunded_remainder',
            'updated_at',
        ],
    )
    logger.info(
        'account-credit: invoice applied user=%s order=%s applied=%s remainder=%s balance=%s',
        user.pk,
        order.pk,
        amount_to_apply,
        remainder,
        new_balance,
    )
    return amount_to_apply, remainder


def record_prepaid_payment_success(
    order_id: int,
    *,
    amount=None,
    gateway_reference: str = '',
) -> tuple[bool, str, Order | None]:
    """Best-effort wrapper for payment success; returns (ok, message, order)."""
    try:
        with transaction.atomic():
            order = Order.objects.select_related('user', 'store').select_for_update().get(pk=order_id)
            if order.payment_status == Order.PaymentStatus.PAID:
                return True, 'Payment already recorded.', order
            if order.status == Order.Status.CANCELLED:
                return False, 'Cancelled orders cannot accept payment.', order
            credit_user_for_prepaid_order(
                order,
                amount=Decimal(str(amount)) if amount is not None else None,
                gateway_reference=gateway_reference,
            )
            order.refresh_from_db()
            return True, 'Payment recorded and account credited.', order
    except Order.DoesNotExist:
        return False, 'Order not found.', None
    except ValueError as exc:
        return False, str(exc), None
    except Exception as exc:
        logger.exception('account-credit: payment success failed order=%s', order_id)
        return False, str(exc), None
