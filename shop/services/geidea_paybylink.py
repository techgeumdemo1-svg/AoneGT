"""
Geidea Pay by Link and Refund service.

Provides:
  - create_geidea_payment_link(order)   → generate eInvoice hosted payment URL
  - cancel_geidea_payment_link(order)   → void an unexpired payment link (best-effort)
  - refund_geidea_payment(order_return, amount) → issue card refund via Geidea Refund API

All API credentials and URLs are read from Django settings (environment variables).
No hardcoded URLs or credentials anywhere in this module.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import uuid as uuid_module
from datetime import date, datetime, timedelta
from decimal import Decimal

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class GeideaPayLinkError(Exception):
    """Raised when pay-by-link creation or cancellation fails."""


class GeideaRefundError(Exception):
    """Raised when the Geidea Refund API call fails."""


class GeideaRefundAlreadyProcessedError(GeideaRefundError):
    """
    Raised when a refund is requested but geidea_refund_id is already set
    on the OrderReturn — idempotency guard to prevent double refunds.
    """


# ---------------------------------------------------------------------------
# Internal signature helpers (used only for refund — not for eInvoice creation)
# ---------------------------------------------------------------------------

def _build_refund_signature(
    api_password: str,
    timestamp: str,
    public_key: str,
    amount_str: str,
    order_id: str,
) -> str:
    """
    Refund HMAC-SHA256 signature.
    Per official Geidea docs:
    Concatenation: TimeStamp + MerchantPublicKey + RefundAmount(2dp) + OrderId
    Key: api_password bytes
    Returns Base64-encoded digest.
    """
    concat = f"{timestamp}{public_key}{amount_str}{order_id}"
    return base64.b64encode(
        hmac.new(
            key=api_password.encode('utf-8'),
            msg=concat.encode('utf-8'),
            digestmod=hashlib.sha256,
        ).digest()
    ).decode('utf-8')


# ---------------------------------------------------------------------------
# Public API: Link generation
# ---------------------------------------------------------------------------

def create_geidea_payment_link(order) -> str:
    """
    Create a Geidea eInvoice payment link for a pay_by_link order.

    Per official Geidea docs the eInvoice API payload is:
      top-level: amount, currency, customer, eInvoiceDetails, expiryDate
      callbackUrl goes INSIDE eInvoiceDetails (key: 'callbackurl')
      No timestamp/signature at top level (those are HPP session API fields only)

    Idempotent: if order.geidea_paylink_url is already set, returns it without
    calling the API again.

    Returns:
        str: The hosted payment link URL.

    Raises:
        GeideaPayLinkError: On any configuration, network, or API response error.
    """
    paylink_url = (getattr(settings, 'GEIDEA_PAYLINK_URL', '') or '').strip()
    if not paylink_url:
        raise GeideaPayLinkError(
            'GEIDEA_PAYLINK_URL is not configured. Set it in environment variables.'
        )

    # Idempotency — return existing URL without hitting the API
    existing_url = (order.geidea_paylink_url or '').strip()
    if existing_url:
        logger.info(
            'geidea-paybylink: returning cached link order=%s url=%s',
            order.pk, existing_url,
        )
        return existing_url

    # Generate UUID for merchant reference if not already set
    if not order.geidea_merchant_ref:
        order.geidea_merchant_ref = uuid_module.uuid4()
        order.save(update_fields=['geidea_merchant_ref'])

    merchant_ref = str(order.geidea_merchant_ref)
    currency = (order.currency or 'AED').strip() or 'AED'
    public_key = settings.GEIDEA_PUBLIC_KEY
    api_password = settings.GEIDEA_API_PASSWORD

    # Callback URL: use pay-by-link specific URL, fall back to standard callback URL
    callback_url = (getattr(settings, 'GEIDEA_PAYLINK_CALLBACK_URL', '') or '').strip()
    if not callback_url:
        callback_url = (getattr(settings, 'GEIDEA_CALLBACK_URL', '') or '').strip()

    expiry_days = getattr(settings, 'GEIDEA_PAYLINK_EXPIRY_DAYS', 7)
    expiry_date = (date.today() + timedelta(days=expiry_days)).isoformat()

    # amount must equal sum of eInvoiceItems totals — use a single summary line
    total_float = round(float(order.total), 2)

    einvoice_items = [
        {
            'description': f'Order #{order.pk}',
            'price': total_float,
            'quantity': 1,
            'total': total_float,
            'itemDiscountType': 'Amount',
            'taxType': 'Amount',
            'priceWithDiscount': total_float,
            'priceTax': 0,
            'priceTotal': total_float,
            'itemDiscount': 0,
            'tax': 0,
            'totalWithoutTax': total_float,
            'totalTax': 0,
        }
    ]

    # Build payload exactly as per official Geidea eInvoice API docs
    payload = {
        'amount': total_float,
        'currency': currency,
        'expiryDate': expiry_date,
        'customer': {
            'name': (order.shipping_name or '').strip() or 'Customer',
            'email': (getattr(order, 'user', None) and order.user.email) or '',
        },
        'eInvoiceDetails': {
            'subtotal': total_float,
            'grandTotal': total_float,
            'subtotalWithoutTax': total_float,
            'subtotalTax': 0,
            'extraCharges': 0,
            'extraChargesType': 'Amount',
            'invoiceDiscount': 0,
            'invoiceDiscountType': 'Amount',
            'type': 'Detailed',
            'collectCustomersBillingShippingAddress': False,
            'preAuthorizeAmount': False,
            'language': 'EN',
            'merchantReferenceId': merchant_ref,
            'callbackurl': callback_url,
            'eInvoiceItems': einvoice_items,
        },
    }

    logger.info(
        'geidea-paybylink: creating link order=%s merchant_ref=%s amount=%s',
        order.pk, merchant_ref, total_float,
    )

    try:
        response = requests.post(
            paylink_url,
            json=payload,
            auth=(public_key, api_password),
            timeout=30,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout:
        logger.error(
            'geidea-paybylink: timeout order=%s merchant_ref=%s',
            order.pk, merchant_ref,
        )
        raise GeideaPayLinkError('Payment link creation timed out. Please retry.')
    except requests.exceptions.RequestException as exc:
        logger.error(
            'geidea-paybylink: request failed order=%s merchant_ref=%s error=%s',
            order.pk, merchant_ref, exc,
        )
        raise GeideaPayLinkError('Payment link creation failed. Please retry.')

    try:
        data = response.json()
    except ValueError:
        logger.error(
            'geidea-paybylink: invalid JSON response order=%s status=%s body=%s',
            order.pk, response.status_code, response.text,
        )
        raise GeideaPayLinkError('Payment link creation failed — invalid response.')

    response_code = data.get('responseCode', '')
    detailed_code = data.get('detailedResponseCode', '')
    if response_code != '000' or detailed_code != '000':
        logger.error(
            'geidea-paybylink: non-success response order=%s responseCode=%s '
            'detailedResponseCode=%s body=%s',
            order.pk, response_code, detailed_code, data,
        )
        raise GeideaPayLinkError(
            f'Geidea eInvoice error: {response_code}/{detailed_code} — '
            f'{data.get("responseMessage", "")}'
        )

    # Extract link and intent ID from response
    intent = data.get('paymentIntent') or {}
    payment_link = (
        intent.get('link')
        or intent.get('paymentLink')
        or data.get('link')
        or ''
    )
    payment_intent_id = (
        intent.get('paymentIntentId')
        or intent.get('id')
        or data.get('paymentIntentId')
        or ''
    )

    if not payment_link:
        logger.error(
            'geidea-paybylink: missing link in response order=%s data=%s',
            order.pk, data,
        )
        raise GeideaPayLinkError('Geidea returned no payment link URL.')

    order.geidea_paylink_url = str(payment_link)[:500]
    order.geidea_paylink_intent_id = str(payment_intent_id)[:255]
    order.save(update_fields=['geidea_paylink_url', 'geidea_paylink_intent_id', 'updated_at'])

    logger.info(
        'geidea-paybylink: link created order=%s merchant_ref=%s intent_id=%s url=%s',
        order.pk, merchant_ref, payment_intent_id, payment_link,
    )
    return str(payment_link)


# ---------------------------------------------------------------------------
# Public API: Link cancellation (best-effort)
# ---------------------------------------------------------------------------

def cancel_geidea_payment_link(order) -> None:
    """
    Cancel an unexpired Geidea eInvoice payment link.

    Best-effort: logs errors but never raises.
    No-op if order.geidea_paylink_intent_id is empty.
    """
    intent_id = (order.geidea_paylink_intent_id or '').strip()
    if not intent_id:
        logger.warning(
            'geidea-paybylink: cancel skipped — no geidea_paylink_intent_id order=%s',
            order.pk,
        )
        return

    paylink_url = (getattr(settings, 'GEIDEA_PAYLINK_URL', '') or '').strip()
    if not paylink_url:
        logger.warning(
            'geidea-paybylink: cancel skipped — GEIDEA_PAYLINK_URL not configured order=%s',
            order.pk,
        )
        return

    cancel_url = f'{paylink_url.rstrip("/")}/{intent_id}'
    public_key = settings.GEIDEA_PUBLIC_KEY
    api_password = settings.GEIDEA_API_PASSWORD

    try:
        response = requests.delete(
            cancel_url,
            auth=(public_key, api_password),
            timeout=15,
        )
        if response.status_code in (200, 204):
            logger.info(
                'geidea-paybylink: link cancelled order=%s intent_id=%s',
                order.pk, intent_id,
            )
        else:
            logger.error(
                'geidea-paybylink: cancel unexpected status order=%s '
                'intent_id=%s status=%s body=%s',
                order.pk, intent_id, response.status_code, response.text[:200],
            )
    except Exception as exc:
        logger.error(
            'geidea-paybylink: cancel failed order=%s intent_id=%s error=%s',
            order.pk, intent_id, exc,
        )


# ---------------------------------------------------------------------------
# Public API: Card refund
# ---------------------------------------------------------------------------

def refund_geidea_payment(order_return, amount: Decimal) -> str:
    """
    Issue a card refund via Geidea Refund API.

    Returns:
        str: The Geidea refund transaction ID.

    Raises:
        GeideaRefundAlreadyProcessedError: If geidea_refund_id already set.
        GeideaRefundError: On configuration, validation, network, or API errors.
    """
    if (order_return.geidea_refund_id or '').strip():
        raise GeideaRefundAlreadyProcessedError(
            f'Refund already processed for return #{order_return.pk}. '
            f'geidea_refund_id={order_return.geidea_refund_id}'
        )

    refund_url = (getattr(settings, 'GEIDEA_REFUND_URL', '') or '').strip()
    if not refund_url:
        raise GeideaRefundError(
            'GEIDEA_REFUND_URL is not configured. Set it in environment variables.'
        )

    order = order_return.order
    gateway_reference = (order.gateway_reference or '').strip()
    if not gateway_reference:
        raise GeideaRefundError(
            'No gateway_reference on order — cannot issue card refund. '
            'This order may not have been paid via Geidea.'
        )

    amount = Decimal(str(amount)).quantize(Decimal('0.01'))
    if amount <= 0:
        raise GeideaRefundError('Refund amount must be greater than zero.')

    public_key = settings.GEIDEA_PUBLIC_KEY
    api_password = settings.GEIDEA_API_PASSWORD
    # Timestamp format per Geidea refund docs: "3/18/2024 5:16:48 AM" (no leading zeros)
    now = datetime.utcnow()
    timestamp = f"{now.month}/{now.day}/{now.year} {now.strftime('%I').lstrip('0') or '12'}:{now.strftime('%M')}:{now.strftime('%S')} {now.strftime('%p')}"
    amount_str = f"{float(amount):.2f}"

    signature = _build_refund_signature(
        api_password, timestamp, public_key, amount_str, gateway_reference,
    )

    merchant_ref = (
        str(order.geidea_merchant_ref)
        if order.geidea_merchant_ref
        else str(uuid_module.uuid4())
    )

    payload = {
        'orderId': gateway_reference,
        'refundAmount': round(float(amount), 2),
        'merchantReferenceId': merchant_ref,
        'timestamp': timestamp,
        'signature': signature,
    }

    logger.info(
        'geidea-refund: initiating refund return=%s order=%s amount=%s order_id=%s',
        order_return.pk, order.pk, amount_str, gateway_reference,
    )

    try:
        response = requests.post(
            refund_url,
            json=payload,
            auth=(public_key, api_password),
            timeout=30,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout:
        logger.error('geidea-refund: timeout return=%s order=%s', order_return.pk, order.pk)
        raise GeideaRefundError('Refund API timed out. Please retry.')
    except requests.exceptions.RequestException as exc:
        logger.error(
            'geidea-refund: request failed return=%s order=%s error=%s',
            order_return.pk, order.pk, exc,
        )
        raise GeideaRefundError('Refund API request failed. Please retry.')

    try:
        data = response.json()
    except ValueError:
        logger.error(
            'geidea-refund: invalid JSON response return=%s order=%s status=%s body=%s',
            order_return.pk, order.pk, response.status_code, response.text,
        )
        raise GeideaRefundError('Refund API returned an invalid response.')

    response_code = data.get('responseCode', '')
    detailed_code = data.get('detailedResponseCode', '')
    if response_code != '000' or detailed_code != '000':
        logger.error(
            'geidea-refund: non-success response return=%s order=%s '
            'responseCode=%s detailedResponseCode=%s',
            order_return.pk, order.pk, response_code, detailed_code,
        )
        raise GeideaRefundError(
            f'Geidea refund error: {response_code}/{detailed_code} — '
            f'{data.get("responseMessage", "")}'
        )

    # Extract refund transaction ID from response transactions list
    refund_transaction_id = ''
    order_data = data.get('order') or {}
    transactions = order_data.get('transactions') or []
    for txn in transactions:
        if isinstance(txn, dict) and txn.get('type') == 'Refund':
            refund_transaction_id = str(txn.get('transactionId') or '').strip()
            if refund_transaction_id:
                break

    if not refund_transaction_id:
        refund_transaction_id = str(
            order_data.get('orderId') or data.get('orderId') or ''
        ).strip()

    if not refund_transaction_id:
        logger.warning(
            'geidea-refund: no refund transaction ID in response return=%s order=%s',
            order_return.pk, order.pk,
        )
        refund_transaction_id = f'geidea-refund-{order_return.pk}'

    order_return.geidea_refund_id = refund_transaction_id[:255]
    order_return.save(update_fields=['geidea_refund_id', 'updated_at'])

    logger.info(
        'geidea-refund: success return=%s order=%s amount=%s refund_id=%s',
        order_return.pk, order.pk, amount_str, refund_transaction_id,
    )
    return refund_transaction_id
