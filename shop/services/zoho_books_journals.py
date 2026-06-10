"""
Zoho Books journal automation service.

Creates two double-entry journal entries in Zoho Books after a successful payment:

  Journal 1 — Payment Charge:
    DEBIT  charge_account   (payment processing expense)
    CREDIT deposit_account  (bank account)
    Amount = order.total × charge_rate / 100

  Journal 2 — VAT on Charge:
    DEBIT  vat_account      (VAT expense sub-account)
    CREDIT deposit_account  (bank account)
    Amount = journal_1_amount × vat_rate / 100

All rate and account ID values are sourced from ZohoBooksStoreConfig.
This service is strictly best-effort — all exceptions are caught and logged.
It never raises to callers, and journal failures never affect order fields.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone as dj_tz

logger = logging.getLogger(__name__)


def _quantize(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _get_journal_config(config, payment_method: str) -> dict | None:
    """
    Return a dict with charge_rate, vat_rate, and enabled flag
    for the given payment method, reading from ZohoBooksStoreConfig.
    Returns None if the payment method is not supported for journals.
    """
    from shop.models import Order

    if payment_method == Order.PaymentMethod.PAYMENT_GATEWAY.value:
        return {
            'charge_rate': config.gateway_charge_rate,
            'vat_rate': config.gateway_vat_rate,
            'enabled': config.journal_gateway_enabled,
        }
    if payment_method == Order.PaymentMethod.PAY_BY_LINK.value:
        return {
            'charge_rate': config.paylink_charge_rate,
            'vat_rate': config.paylink_vat_rate,
            'enabled': config.journal_paylink_enabled,
        }
    if payment_method in (
        Order.PaymentMethod.CARD_ON_DELIVERY.value,
        Order.PaymentMethod.CASH_ON_DELIVERY.value,
    ):
        return {
            'charge_rate': config.cod_charge_rate,
            'vat_rate': config.cod_vat_rate,
            'enabled': config.journal_cod_enabled,
        }
    return None


def _post_journal(order, *, debit_account_id: str, credit_account_id: str,
                  amount: Decimal, journal_date: date, notes: str) -> str:
    """
    Call POST /books/v3/journals and return the journal_id string.
    Raises ZohoBooksError on API failure.
    """
    from shop.services.zoho_books import _books_request

    body = {
        'journal_date': journal_date.isoformat(),
        'notes': notes,
        'line_items': [
            {
                'account_id': debit_account_id,
                'debit_or_credit': 'debit',
                'amount': float(amount),
            },
            {
                'account_id': credit_account_id,
                'debit_or_credit': 'credit',
                'amount': float(amount),
            },
        ],
    }

    payload = _books_request('POST', 'journals', store=order.store, json_data=body)
    journal = payload.get('journal') or {}
    journal_id = str(journal.get('journal_id') or '').strip()
    return journal_id


def _write_journal_log(
    order,
    *,
    journal_type: str,
    payment_method: str,
    rate_used: Decimal,
    base_amount: Decimal,
    journal_amount: Decimal,
    journal_date: date,
    zoho_journal_id: str = '',
    error: str = '',
) -> None:
    """Write a ZohoBooksJournalLog record. Uses update_or_create for safety."""
    from shop.models import ZohoBooksJournalLog
    try:
        ZohoBooksJournalLog.objects.update_or_create(
            order=order,
            journal_type=journal_type,
            defaults={
                'payment_method': payment_method,
                'rate_used': rate_used,
                'base_amount': base_amount,
                'journal_amount': journal_amount,
                'journal_date': journal_date,
                'zoho_journal_id': zoho_journal_id,
                'error': error,
            },
        )
    except Exception as exc:
        logger.exception(
            'zoho-journals: could not write journal log order=%s type=%s error=%s',
            order.pk, journal_type, exc,
        )


def create_payment_journals_for_order(order, payment_method: str) -> None:
    """
    Best-effort: create Payment_Charge_Journal then VAT_Charge_Journal for the order.

    Never raises. All outcomes recorded in ZohoBooksJournalLog.

    Args:
        order: A saved Order instance.
        payment_method: The payment method string (Order.PaymentMethod value).
    """
    from shop.models import ZohoBooksJournalLog

    # ── 1. Fetch config ────────────────────────────────────────────────────
    try:
        config = order.store.zoho_books_config
    except Exception:
        logger.info(
            'zoho-journals: no ZohoBooksStoreConfig for store=%s order=%s — skipping',
            order.store_id, order.pk,
        )
        return

    # ── 2. Resolve rates and enable flag ──────────────────────────────────
    journal_config = _get_journal_config(config, payment_method)
    if journal_config is None:
        logger.info(
            'zoho-journals: payment_method=%s not supported for journals order=%s — skipping',
            payment_method, order.pk,
        )
        return

    if not journal_config['enabled']:
        logger.info(
            'zoho-journals: journal disabled for method=%s store=%s order=%s — skipping',
            payment_method, order.store_id, order.pk,
        )
        return

    # ── 3. Validate required account IDs ──────────────────────────────────
    charge_account_id = (config.charge_account_id or '').strip()
    deposit_account_id = (config.deposit_account_id or '').strip()
    vat_account_id = (config.vat_account_id or '').strip()

    if not charge_account_id or not deposit_account_id:
        logger.info(
            'zoho-journals: charge_account_id or deposit_account_id empty order=%s — skipping',
            order.pk,
        )
        return

    today = dj_tz.now().date()
    charge_rate = Decimal(str(config.gateway_charge_rate if payment_method == 'payment_gateway'
                               else config.paylink_charge_rate if payment_method == 'pay_by_link'
                               else config.cod_charge_rate))
    vat_rate = Decimal(str(config.gateway_vat_rate if payment_method == 'payment_gateway'
                            else config.paylink_vat_rate if payment_method == 'pay_by_link'
                            else config.cod_vat_rate))

    order_total = _quantize(order.total)
    charge_amount = _quantize(order_total * charge_rate / Decimal('100'))

    # ── 4. Journal 1: Payment Charge ──────────────────────────────────────
    # Idempotency: skip if already logged for this (order, journal_type)
    if ZohoBooksJournalLog.objects.filter(
        order=order,
        journal_type=ZohoBooksJournalLog.JournalType.PAYMENT_CHARGE,
    ).exists():
        logger.info(
            'zoho-journals: payment_charge already logged order=%s — skipping', order.pk,
        )
    else:
        journal_1_id = ''
        journal_1_error = ''
        if charge_amount > Decimal('0'):
            try:
                notes = (
                    f'AoneGT order #{order.pk} - payment charge ({payment_method})'
                )
                journal_1_id = _post_journal(
                    order,
                    debit_account_id=charge_account_id,
                    credit_account_id=deposit_account_id,
                    amount=charge_amount,
                    journal_date=today,
                    notes=notes,
                )
                logger.info(
                    'zoho-journals: payment_charge created order=%s journal_id=%s amount=%s',
                    order.pk, journal_1_id, charge_amount,
                )
            except Exception as exc:
                journal_1_error = str(exc)[:5000]
                logger.exception(
                    'zoho-journals: payment_charge failed order=%s error=%s',
                    order.pk, exc,
                )
        else:
            logger.info(
                'zoho-journals: charge_amount is zero order=%s — skipping payment_charge journal',
                order.pk,
            )

        _write_journal_log(
            order,
            journal_type=ZohoBooksJournalLog.JournalType.PAYMENT_CHARGE,
            payment_method=payment_method,
            rate_used=charge_rate,
            base_amount=order_total,
            journal_amount=charge_amount,
            journal_date=today,
            zoho_journal_id=journal_1_id,
            error=journal_1_error,
        )

    # ── 5. Journal 2: VAT on Charge ───────────────────────────────────────
    if not vat_account_id:
        logger.info(
            'zoho-journals: vat_account_id empty order=%s — skipping vat_charge journal',
            order.pk,
        )
        return

    if ZohoBooksJournalLog.objects.filter(
        order=order,
        journal_type=ZohoBooksJournalLog.JournalType.VAT_CHARGE,
    ).exists():
        logger.info(
            'zoho-journals: vat_charge already logged order=%s — skipping', order.pk,
        )
        return

    vat_amount = _quantize(charge_amount * vat_rate / Decimal('100'))
    journal_2_id = ''
    journal_2_error = ''

    if vat_amount > Decimal('0'):
        try:
            notes = (
                f'AoneGT order #{order.pk} - VAT on payment charge ({payment_method})'
            )
            journal_2_id = _post_journal(
                order,
                debit_account_id=vat_account_id,
                credit_account_id=deposit_account_id,
                amount=vat_amount,
                journal_date=today,
                notes=notes,
            )
            logger.info(
                'zoho-journals: vat_charge created order=%s journal_id=%s amount=%s',
                order.pk, journal_2_id, vat_amount,
            )
        except Exception as exc:
            journal_2_error = str(exc)[:5000]
            logger.exception(
                'zoho-journals: vat_charge failed order=%s error=%s',
                order.pk, exc,
            )
    else:
        logger.info(
            'zoho-journals: vat_amount is zero order=%s — skipping vat_charge journal',
            order.pk,
        )

    _write_journal_log(
        order,
        journal_type=ZohoBooksJournalLog.JournalType.VAT_CHARGE,
        payment_method=payment_method,
        rate_used=vat_rate,
        base_amount=charge_amount,
        journal_amount=vat_amount,
        journal_date=today,
        zoho_journal_id=journal_2_id,
        error=journal_2_error,
    )
