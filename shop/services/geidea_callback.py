"""
Geidea Payment Gateway — Callback processing service.

Handles the POST callback from Geidea after a user completes or abandons
payment on the Hosted Payment Page (HPP).

This is the most security-critical endpoint in the integration.
Signature verification happens before any database access.
"""

import base64
import hashlib
import hmac
import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction

from shop.models import Order
from shop.services.account_credit import credit_user_for_prepaid_order
from shop.services.card_on_delivery_payment import (
    finalize_card_on_delivery_after_geidea,
    is_card_on_delivery_order,
    record_card_on_delivery_geidea_payment,
)
from shop.services.zoho_books_payment import maybe_create_zoho_books_advance_payment_for_order

logger = logging.getLogger(__name__)


def verify_callback_signature(payload):
    """
    Verify the HMAC-SHA256 signature in the Geidea callback payload.

    Callback signature concatenation per official Geidea docs:
      PublicKey + OrderAmount + OrderCurrency + OrderId + Status + MerchantReferenceId + timeStamp

    The timestamp field name is confirmed as top-level "timeStamp" (capital S).

    Returns True if signature is valid, False otherwise.
    """
    try:
        order_data       = payload["order"]
        received_sig     = payload["signature"]
        merchant_pub_key = order_data["merchantPublicKey"]
        configured_key = (getattr(settings, 'GEIDEA_PUBLIC_KEY', '') or '').strip()
        if configured_key and merchant_pub_key != configured_key:
            logger.error(
                'Geidea callback — merchantPublicKey mismatch. expected=%s received=%s',
                configured_key,
                merchant_pub_key,
            )
            return False
        amount_str       = f"{float(order_data['totalAmount']):.2f}"
        currency         = order_data["currency"]
        geidea_order_id  = order_data["orderId"]
        status           = order_data["status"]
        merchant_ref     = order_data["merchantReferenceId"]

        # Confirmed field name from official Geidea docs: top-level "timeStamp"
        timestamp = payload.get("timeStamp", "")

        concat = (
            f"{merchant_pub_key}{amount_str}{currency}"
            f"{geidea_order_id}{status}{merchant_ref}{timestamp}"
        )
        expected_sig = base64.b64encode(
            hmac.new(
                key=settings.GEIDEA_API_PASSWORD.encode('utf-8'),
                msg=concat.encode('utf-8'),
                digestmod=hashlib.sha256,
            ).digest()
        ).decode('utf-8')

        return hmac.compare_digest(expected_sig, received_sig)

    except (KeyError, TypeError, ValueError) as exc:
        logger.error("Callback signature verification error: %s", exc)
        return False


def process_geidea_callback(payload):
    """
    Process a verified Geidea callback payload.

    Returns a tuple: (http_status_code, message)
    Always returns 200 to Geidea except on signature mismatch (400).
    Geidea retries on non-200 responses — we must not let it retry on
    legitimate failures like order-not-found or already-paid.

    Steps:
      A — Signature verification (before any DB access)
      B — Check payment result (status and detailedStatus)
      C — Fetch and lock Order, validate, credit user (inside atomic)
      D — Create Zoho advance payment (outside atomic)
    """

    # ── Step A: Signature verification ────────────────────────────────────
    # Log full payload first so we can debug the timestamp issue if needed
    # Log order id only — full payload may contain cardholder data.
    logger.info(
        "Geidea callback received. orderId=%s merchant_ref=%s",
        payload.get('order', {}).get('orderId'),
        payload.get('order', {}).get('merchantReferenceId'),
    )

    if not verify_callback_signature(payload):
        logger.error(
            "Geidea callback signature mismatch. "
            "Possible tampering. payload=%s", payload
        )
        return 400, "Signature mismatch"

    # ── Step B: Extract fields and check payment result ───────────────────
    order_data       = payload["order"]
    geidea_order_id  = order_data["orderId"]
    callback_amount  = order_data["totalAmount"]    # use totalAmount, not amount
    callback_currency= order_data["currency"]
    callback_status  = order_data["status"]         # "Success" or "Failed"
    callback_detailed= order_data["detailedStatus"] # "Paid" or other
    merchant_ref     = order_data["merchantReferenceId"]  # the UUID we sent

    # ⚠️ There is NO top-level responseCode in the callback.
    # responseCode lives inside order.transactions[].codes — do not check it here.
    if callback_status != "Success" or callback_detailed != "Paid":
        logger.info(
            "Geidea callback — payment not successful. "
            "geidea_order_id=%s status=%s detailedStatus=%s",
            geidea_order_id, callback_status, callback_detailed,
        )
        # Return 200 so Geidea stops retrying. Order stays PENDING.
        return 200, "Payment not successful"

    # ── Step C: Fetch, lock, validate, and update Order ───────────────────
    try:
        with transaction.atomic():
            # Look up by geidea_merchant_ref (the UUID we generated and sent)
            try:
                order = Order.objects.select_for_update().get(
                    geidea_merchant_ref=merchant_ref
                )
            except Order.DoesNotExist:
                logger.critical(
                    "Geidea callback — Order not found for merchant_ref=%s "
                    "geidea_order_id=%s",
                    merchant_ref, geidea_order_id,
                )
                return 200, "Order not found"

            # Idempotency — already processed (duplicate callback)
            if order.payment_status == Order.PaymentStatus.PAID:
                logger.info(
                    "Geidea callback — duplicate callback, order already paid. "
                    "order_pk=%s geidea_order_id=%s",
                    order.pk, geidea_order_id,
                )
                return 200, "Already processed"

            # Amount tamper check — use totalAmount
            if Decimal(str(callback_amount)) != order.total:
                logger.error(
                    "Geidea callback — SECURITY ALERT: amount mismatch. "
                    "order_pk=%s expected=%s received=%s",
                    order.pk, order.total, callback_amount,
                )
                return 400, "Amount mismatch"

            # Currency check
            if callback_currency != order.currency:
                logger.error(
                    "Geidea callback — SECURITY ALERT: currency mismatch. "
                    "order_pk=%s expected=%s received=%s",
                    order.pk, order.currency, callback_currency,
                )
                return 400, "Currency mismatch"

            if is_card_on_delivery_order(order):
                record_card_on_delivery_geidea_payment(
                    order,
                    Decimal(str(callback_amount)),
                    gateway_reference=geidea_order_id,
                )
                logger.info(
                    "Geidea callback — card on delivery marked PAID. "
                    "order_pk=%s geidea_order_id=%s amount=%s",
                    order.pk, geidea_order_id, callback_amount,
                )
            else:
                credit_user_for_prepaid_order(
                    order,
                    amount=Decimal(str(callback_amount)),
                    gateway_reference=geidea_order_id,
                )
                logger.info(
                    "Geidea callback — order marked PAID. "
                    "order_pk=%s zoho_so=%s geidea_order_id=%s amount=%s",
                    order.pk, order.zoho_books_salesorder_id,
                    geidea_order_id, callback_amount,
                )

    except Exception as exc:
        logger.critical(
            "Geidea callback — unexpected error during atomic block. "
            "merchant_ref=%s geidea_order_id=%s error=%s",
            merchant_ref, geidea_order_id, exc,
        )
        # Return 200 anyway — do not let Geidea retry indefinitely
        return 200, "Internal error"

    # ── Step D: Post-payment actions (outside atomic block) ─────────────
    try:
        if is_card_on_delivery_order(order):
            finalize_card_on_delivery_after_geidea(order.pk)
        else:
            maybe_create_zoho_books_advance_payment_for_order(order)
    except Exception as exc:
        logger.critical(
            "Geidea callback — post-payment step failed AFTER order marked PAID. "
            "order_pk=%s payment_method=%s amount=%s error=%s "
            "ACTION REQUIRED: reconcile manually.",
            order.pk, order.payment_method, callback_amount, exc,
        )

    return 200, "OK"
