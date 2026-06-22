"""Customer and staff order cancellation: void Zoho SO, refunds, notifications."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone as dj_tz

from shop.models import AccountCreditLedger, Order, PurchasePointsLedger
from shop.serializers import (
    ORDER_CUSTOMER_TRACKING_STAGE_LABELS,
    _effective_customer_tracking_stage,
)
from shop.services.account_credit import get_user_credit_balance
from shop.services.order_status_notifications import notify_order_tracking_status_change
from shop.services.order_sync_state import apply_order_sync_transition
from shop.services.order_tracking import (
    TRACKING_HISTORY_CANCELLED_KEY,
    record_tracking_stage,
)
from shop.services.return_refund import return_refund_amount
from shop.services.zoho_books import ZohoBooksError
from shop.services.zoho_books_payment import is_prepaid_at_checkout_payment_method
from shop.services.zoho_books_sales_order import void_zoho_books_sales_order_for_order

logger = logging.getLogger(__name__)
User = get_user_model()

_GEIDEA_CARD_PAYMENT_METHODS = frozenset({
    Order.PaymentMethod.PAY_BY_LINK.value,
    Order.PaymentMethod.PAYMENT_GATEWAY.value,
    Order.PaymentMethod.CARD_ON_DELIVERY.value,
})


def order_cancellation_blocked_reason(order: Order, *, customer: bool = False) -> str | None:
    """Return an error message when cancellation is not allowed, else None."""
    if order.status == Order.Status.CANCELLED:
        return 'Order is already cancelled.'

    if (order.zoho_books_invoice_id or '').strip():
        return 'Cannot cancel: invoice already exists for this order.'

    if customer:
        stage = _effective_customer_tracking_stage(order)
        if stage != 'pending':
            label = ORDER_CUSTOMER_TRACKING_STAGE_LABELS.get(stage, stage.replace('_', ' ').title())
            return f'Order cannot be cancelled once it is {label}.'

    return None


def _is_geidea_card_payment(order: Order) -> bool:
    return (order.payment_method or '').strip() in _GEIDEA_CARD_PAYMENT_METHODS


def _order_return_geidea_refund_total(order: Order) -> Decimal:
    """Sum Geidea card refunds already issued via approved returns."""
    from shop.services.geidea_paybylink import _is_geidea_refund_processing

    total = Decimal('0.00')
    returns = order.returns.exclude(geidea_refund_id='').prefetch_related('lines__order_item')
    for ret in returns:
        refund_id = (ret.geidea_refund_id or '').strip()
        if not refund_id or _is_geidea_refund_processing(refund_id):
            continue
        total += return_refund_amount(ret)
    return total.quantize(Decimal('0.01'))


def _geidea_cancel_refund_amount(order: Order) -> Decimal:
    if is_prepaid_at_checkout_payment_method(order.payment_method):
        prepaid = Decimal(str(order.prepaid_credited_amount or 0)).quantize(Decimal('0.01'))
        full = prepaid if prepaid > 0 else Decimal(str(order.total or 0)).quantize(Decimal('0.01'))
    else:
        full = Decimal(str(order.total or 0)).quantize(Decimal('0.01'))
    remaining = (full - _order_return_geidea_refund_total(order)).quantize(Decimal('0.01'))
    return max(remaining, Decimal('0.00'))


def _order_needs_geidea_cancel_refund(order: Order) -> bool:
    if order.payment_status != Order.PaymentStatus.PAID:
        return False
    if not _is_geidea_card_payment(order):
        return False
    if not (order.gateway_reference or '').strip():
        return False
    from shop.services.geidea_paybylink import _is_geidea_refund_processing

    current = (order.geidea_refund_id or '').strip()
    if current and not _is_geidea_refund_processing(current):
        return False
    if _geidea_cancel_refund_amount(order) <= 0:
        return False
    return True


def _restore_loyalty_on_cancel(locked: Order) -> None:
    """Reverse points redeemed at checkout and remove earned points for this order."""
    user = User.objects.select_for_update().get(pk=locked.user_id)
    update_fields: list[str] = []

    redeemed = int(locked.loyalty_points_redeemed or 0)
    if redeemed > 0:
        user.points_balance = int(user.points_balance or 0) + redeemed
        update_fields.append('points_balance')

    try:
        ledger = PurchasePointsLedger.objects.select_for_update().get(order=locked)
    except PurchasePointsLedger.DoesNotExist:
        ledger = None

    if ledger:
        awarded = int(ledger.points_awarded or 0)
        if awarded > 0:
            user.points_balance = max(int(user.points_balance or 0) - awarded, 0)
            if 'points_balance' not in update_fields:
                update_fields.append('points_balance')
        ledger.delete()

    if update_fields:
        user.save(update_fields=update_fields)


def _record_prepaid_cancel_ledger(locked: Order) -> None:
    """Prepaid payments stay on account credit; record audit row for customer cancel."""
    if locked.payment_status != Order.PaymentStatus.PAID:
        return
    prepaid = Decimal(str(locked.prepaid_credited_amount or 0)).quantize(Decimal('0.01'))
    if prepaid <= 0:
        return
    if Decimal(str(locked.credit_applied_on_invoice or 0)).quantize(Decimal('0.01')) > 0:
        return
    if not is_prepaid_at_checkout_payment_method(locked.payment_method):
        return

    AccountCreditLedger.objects.create(
        user=locked.user,
        order=locked,
        kind=AccountCreditLedger.Kind.ORDER_CANCEL,
        amount=Decimal('0'),
        balance_after=get_user_credit_balance(locked.user),
        note=(
            f'Order #{locked.pk} cancelled; '
            f'{prepaid} AED remains on account credit.'
        ),
    )


def _reverse_prepaid_credit_after_card_refund(locked: Order, refund_amount: Decimal) -> None:
    """Deduct prepaid account credit after Geidea refunds the original card charge."""
    if not is_prepaid_at_checkout_payment_method(locked.payment_method):
        return
    if locked.payment_status != Order.PaymentStatus.PAID:
        return

    prepaid = Decimal(str(locked.prepaid_credited_amount or 0)).quantize(Decimal('0.01'))
    if prepaid <= 0:
        return

    user = User.objects.select_for_update().get(pk=locked.user_id)
    current = get_user_credit_balance(user)
    deduct = min(prepaid, current, refund_amount)
    if deduct <= 0:
        return

    new_balance = current - deduct
    user.credit_balance_aed = new_balance
    user.save(update_fields=['credit_balance_aed'])

    AccountCreditLedger.objects.create(
        user=user,
        order=locked,
        kind=AccountCreditLedger.Kind.ORDER_CANCEL,
        amount=-deduct,
        balance_after=new_balance,
        note=(
            f'Order #{locked.pk} cancelled; '
            f'{deduct} AED refunded to card via Geidea.'
        ),
    )


def cancel_order(order_id: int, *, customer: bool = False, notify: bool = True) -> tuple[bool, str]:
    """
    Cancel an order: void Zoho Books sales order, update local status, handle refunds.

    Paid Geidea card orders are refunded to the original card before cancellation completes.
    Customers may cancel only while the order is still Pending (tracking stage).
    """
    try:
        order = Order.objects.select_related('user', 'store').get(pk=order_id)
    except Order.DoesNotExist:
        return False, 'Order not found.'

    blocked = order_cancellation_blocked_reason(order, customer=customer)
    if blocked:
        return False, blocked

    claim_token: str | None = None
    geidea_refund_amount: Decimal | None = None

    with transaction.atomic():
        locked = Order.objects.select_for_update().select_related('user', 'store').get(pk=order_id)
        blocked = order_cancellation_blocked_reason(locked, customer=customer)
        if blocked:
            return False, blocked

        if _order_needs_geidea_cancel_refund(locked):
            geidea_refund_amount = _geidea_cancel_refund_amount(locked)
            from shop.services.geidea_paybylink import (
                GeideaRefundAlreadyProcessedError,
                GeideaRefundError,
                claim_order_geidea_refund,
            )

            try:
                claim_token = claim_order_geidea_refund(locked)
            except GeideaRefundAlreadyProcessedError:
                claim_token = None
                geidea_refund_amount = None
            except GeideaRefundError as exc:
                return False, str(exc)

    if claim_token and geidea_refund_amount and geidea_refund_amount > 0:
        from shop.services.geidea_paybylink import (
            GeideaRefundError,
            finalize_order_geidea_refund,
            issue_geidea_card_refund,
            release_order_geidea_refund_claim,
        )

        order = Order.objects.select_related('store').get(pk=order_id)
        try:
            refund_id = issue_geidea_card_refund(order, geidea_refund_amount)
            finalize_order_geidea_refund(order_id, claim_token, refund_id)
        except GeideaRefundError as exc:
            release_order_geidea_refund_claim(order_id, claim_token)
            logger.error(
                'order-cancel: Geidea refund failed order=%s error=%s',
                order_id,
                exc,
            )
            return False, str(exc)
        except Exception:
            release_order_geidea_refund_claim(order_id, claim_token)
            raise

    previous_stage = order.customer_tracking_stage

    try:
        with transaction.atomic():
            locked = Order.objects.select_for_update().select_related('user', 'store').get(pk=order_id)

            blocked = order_cancellation_blocked_reason(locked, customer=customer)
            if blocked:
                return False, blocked

            if (
                locked.payment_method == Order.PaymentMethod.PAY_BY_LINK
                and locked.payment_status != Order.PaymentStatus.PAID
            ):
                try:
                    from shop.services.geidea_paybylink import cancel_geidea_payment_link

                    cancel_geidea_payment_link(locked)
                except Exception as exc:
                    logger.error(
                        'order-cancel: cancel_geidea_payment_link failed order=%s error=%s',
                        order_id,
                        exc,
                    )

            if (locked.zoho_books_salesorder_id or '').strip():
                void_zoho_books_sales_order_for_order(locked)

            apply_order_sync_transition(locked, Order.Status.CANCELLED)
            record_tracking_stage(locked, TRACKING_HISTORY_CANCELLED_KEY, save=False)
            locked.save(update_fields=['tracking_stage_history', 'updated_at'])

            if geidea_refund_amount and geidea_refund_amount > 0 and (locked.geidea_refund_id or '').strip():
                _reverse_prepaid_credit_after_card_refund(locked, geidea_refund_amount)
            else:
                _record_prepaid_cancel_ledger(locked)
            _restore_loyalty_on_cancel(locked)

    except ZohoBooksError as exc:
        logger.exception('order-cancel: Zoho void failed order=%s (%s)', order_id, exc)
        Order.objects.filter(pk=order_id).update(
            zoho_books_salesorder_error=str(exc)[:5000],
            updated_at=dj_tz.now(),
        )
        return False, str(exc)
    except ValueError as exc:
        return False, str(exc)
    except Exception as exc:
        logger.exception('order-cancel: unexpected error order=%s', order_id)
        return False, str(exc)

    if notify:
        try:
            order = Order.objects.select_related('user', 'store').get(pk=order_id)
            notify_order_tracking_status_change(
                order,
                stage_key='cancelled',
                previous_stage=previous_stage,
            )
        except Exception:
            logger.exception('order-cancel: notification failed order=%s', order_id)

    if geidea_refund_amount and geidea_refund_amount > 0:
        try:
            order = Order.objects.select_related('user').get(pk=order_id)
            from shop.services.order_email import send_refund_email

            send_refund_email(order, order.user, geidea_refund_amount)
        except Exception:
            logger.exception('order-cancel: refund email failed order=%s', order_id)

        return (
            True,
            (
                f'Order cancelled. Refund of {geidea_refund_amount} AED will be credited '
                f'to the original payment card within 3–7 business days.'
            ),
        )

    return True, 'Order cancelled successfully.'
