import base64
import hashlib
import hmac
import logging
import uuid as uuid_module
from datetime import datetime

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class GeideaSessionError(Exception):
    """Raised when Geidea session creation fails for any reason."""
    pass


def create_geidea_session(order):
    """
    Call the Geidea Create Session API server-to-server and return the session_id.

    Uses order.geidea_merchant_ref (a UUID) as merchantReferenceId because
    Geidea requires a valid UUID. The UUID is generated on the first call and
    stored on the order so callbacks can look it up.

    Args:
        order: A fully saved Order instance with zoho_books_salesorder_id populated.

    Returns:
        str: The session_id from Geidea (response["session"]["id"]).

    Raises:
        GeideaSessionError: If the API call fails, times out, returns a
                            non-success response code, or returns malformed JSON.
    """
    # Generate a UUID for Geidea if not already set.
    # Geidea requires merchantReferenceId to be a valid UUID.
    # We store it on the order so the callback can look it up.
    if not order.geidea_merchant_ref:
        order.geidea_merchant_ref = uuid_module.uuid4()
        order.save(update_fields=['geidea_merchant_ref'])

    merchant_ref = str(order.geidea_merchant_ref)
    amount_str   = f"{float(order.total):.2f}"
    currency     = order.currency

    # Timestamp format confirmed from Geidea PHP sample: Y/m/d H:i:s
    timestamp = datetime.utcnow().strftime("%Y/%m/%d %H:%M:%S")

    # Signature: HMAC-SHA256, Base64-encoded.
    # Concatenation order: PublicKey + amount(2dp) + Currency + merchantReferenceId + timestamp
    concat = f"{settings.GEIDEA_PUBLIC_KEY}{amount_str}{currency}{merchant_ref}{timestamp}"
    signature = base64.b64encode(
        hmac.new(
            key=settings.GEIDEA_API_PASSWORD.encode('utf-8'),
            msg=concat.encode('utf-8'),
            digestmod=hashlib.sha256,
        ).digest()
    ).decode('utf-8')

    payload = {
        "amount":              round(float(order.total), 2),
        "currency":            currency,
        "timestamp":           timestamp,
        "merchantReferenceId": merchant_ref,
        "signature":           signature,
        "paymentOperation":    "Pay",
        "callbackUrl":         settings.GEIDEA_CALLBACK_URL,
        "language":            "en",
    }

    # --- Make the HTTP request ---
    try:
        response = requests.post(
            settings.GEIDEA_SESSION_URL,
            json=payload,
            auth=(settings.GEIDEA_PUBLIC_KEY, settings.GEIDEA_API_PASSWORD),
            timeout=30,
        )
        response.raise_for_status()

    except requests.exceptions.Timeout:
        logger.error(
            "Geidea session creation timed out. order_pk=%s zoho_so=%s",
            order.pk, merchant_ref,
        )
        raise GeideaSessionError("Payment initiation timed out, please retry.")

    except requests.exceptions.RequestException as exc:
        logger.error(
            "Geidea session creation request failed. order_pk=%s zoho_so=%s error=%s",
            order.pk, merchant_ref, exc,
        )
        raise GeideaSessionError("Payment initiation failed, please retry.")

    # --- Parse response body ---
    # Kept in a separate try-except because response.json() can fail independently
    # of the HTTP request succeeding (e.g. Geidea returns malformed JSON).
    try:
        response_data = response.json()
    except ValueError:
        logger.error(
            "Geidea session response was not valid JSON. "
            "order_pk=%s zoho_so=%s status_code=%s body=%s",
            order.pk, merchant_ref, response.status_code, response.text,
        )
        raise GeideaSessionError("Payment initiation failed, please retry.")

    # --- Validate response codes ---
    # Both responseCode and detailedResponseCode must be "000" for a valid session.
    if (response_data.get("responseCode") != "000"
            or response_data.get("detailedResponseCode") != "000"):
        logger.error(
            "Geidea session creation returned non-success codes. "
            "order_pk=%s zoho_so=%s responseCode=%s detailedResponseCode=%s",
            order.pk,
            merchant_ref,
            response_data.get("responseCode"),
            response_data.get("detailedResponseCode"),
        )
        raise GeideaSessionError("Payment initiation failed, please retry.")

    # --- Extract session ID ---
    # session.id is nested inside a "session" object — NOT a top-level session_id key.
    try:
        session_id = response_data["session"]["id"]
    except (KeyError, TypeError):
        logger.error(
            "Geidea session response missing session.id. "
            "order_pk=%s zoho_so=%s response=%s",
            order.pk, merchant_ref, response_data,
        )
        raise GeideaSessionError("Payment initiation failed, please retry.")

    logger.info(
        "Geidea session created successfully. order_pk=%s zoho_so=%s session_id=%s",
        order.pk, merchant_ref, session_id,
    )
    return session_id


def fetch_geidea_orders_by_merchant_ref(merchant_ref):
    """
    Fetch all Geidea orders for a given merchantReferenceId.

    Geidea returns an ARRAY — multiple entries exist when the user retried
    payment (each retry = new Geidea order under the same merchantReferenceId).

    Args:
        merchant_ref: The UUID stored in order.geidea_merchant_ref

    Returns:
        list: List of order dicts from Geidea. Empty list on failure.
    """
    try:
        response = requests.get(
            settings.GEIDEA_FETCH_URL,
            params={"MerchantReferenceId": str(merchant_ref)},
            auth=(settings.GEIDEA_PUBLIC_KEY, settings.GEIDEA_API_PASSWORD),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("orders", [])

    except requests.exceptions.Timeout:
        logger.error(
            "Geidea fetch by merchant ref timed out. merchant_ref=%s", merchant_ref
        )
        return []

    except requests.exceptions.RequestException as exc:
        logger.error(
            "Geidea fetch by merchant ref failed. merchant_ref=%s error=%s",
            merchant_ref, exc,
        )
        return []

    except ValueError:
        logger.error(
            "Geidea fetch by merchant ref returned invalid JSON. merchant_ref=%s",
            merchant_ref,
        )
        return []


def reconcile_or_cancel_stale_order(order):
    """
    Called by the APScheduler stale order cleanup job.

    Checks Geidea for payment status of a stale PENDING order and either:
    - Reconciles it as PAID if Geidea has a successful payment (missed callback)
    - Cancels it if Geidea has no successful payment

    Args:
        order: A stale Order instance (payment_gateway, PENDING, >2hrs old)
    """
    from decimal import Decimal
    from django.db import transaction as db_transaction
    from shop.services.account_credit import credit_user_for_prepaid_order
    from shop.services.zoho_books_payment import maybe_create_zoho_books_advance_payment_for_order

    logger.info(
        "Stale order cleanup — checking order. order_pk=%s zoho_so=%s",
        order.pk, order.zoho_books_salesorder_id,
    )

    # If initiate was never called, there is nothing to check on Geidea's side
    if not order.geidea_merchant_ref:
        logger.info(
            "Stale order cleanup — no geidea_merchant_ref, cancelling. order_pk=%s",
            order.pk,
        )
        _cancel_stale_order(order)
        return

    orders_list = fetch_geidea_orders_by_merchant_ref(order.geidea_merchant_ref)

    paid_entry = next(
        (o for o in orders_list
         if o.get("status") == "Success" and o.get("detailedStatus") == "Paid"),
        None
    )

    if paid_entry:
        # Missed callback — reconcile
        logger.warning(
            "Stale order cleanup — missed callback detected. "
            "order_pk=%s geidea_order_id=%s Reconciling.",
            order.pk, paid_entry.get("orderId"),
        )
        try:
            with db_transaction.atomic():
                order = Order.objects.select_for_update().get(pk=order.pk)
                if order.payment_status != Order.PaymentStatus.PAID:
                    credit_user_for_prepaid_order(
                        order,
                        amount=Decimal(str(paid_entry["totalAmount"])),
                        gateway_reference=paid_entry["orderId"],
                    )
            maybe_create_zoho_books_advance_payment_for_order(order)
        except Exception as exc:
            logger.critical(
                "Stale order cleanup — reconciliation failed. order_pk=%s error=%s",
                order.pk, exc,
            )

    else:
        # No successful payment found — cancel
        all_failed = all(
            o.get("status") != "Success"
            for o in orders_list
        ) if orders_list else True

        if all_failed:
            logger.info(
                "Stale order cleanup — no successful payment found, cancelling. "
                "order_pk=%s geidea_entries=%d",
                order.pk, len(orders_list),
            )
            _cancel_stale_order(order)


def _cancel_stale_order(order):
    """Cancel a stale order and void its Zoho Sales Order."""
    from shop.services.order_sync_state import apply_order_sync_transition
    from shop.services.zoho_books_sales_order import void_zoho_books_sales_order_for_order

    # Best-effort: cancel Geidea payment link for pay_by_link orders
    if order.payment_method == Order.PaymentMethod.PAY_BY_LINK:
        try:
            from shop.services.geidea_paybylink import cancel_geidea_payment_link
            cancel_geidea_payment_link(order)
        except Exception as exc:
            logger.error(
                'Stale order cleanup — cancel_geidea_payment_link failed. order_pk=%s error=%s',
                order.pk, exc,
            )
        # DB cancellation proceeds regardless of payment link cancellation result
    try:
        apply_order_sync_transition(order, Order.Status.CANCELLED)
    except Exception as exc:
        logger.error(
            "Stale order cleanup — cancellation failed. order_pk=%s error=%s",
            order.pk, exc,
        )
        return  # Don't try to void Zoho if status transition failed

    try:
        void_zoho_books_sales_order_for_order(order)
    except Exception as exc:
        logger.error(
            "Stale order cleanup — Zoho void failed (order already cancelled). "
            "order_pk=%s error=%s",
            order.pk, exc,
        )
