import base64
import hashlib
import hmac
import logging
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

    Uses order.zoho_books_salesorder_id as merchantReferenceId so every Geidea
    transaction is directly traceable to the Zoho Books Sales Order — which
    matters for refunds and reconciliation.

    Args:
        order: A fully saved Order instance with zoho_books_salesorder_id populated.

    Returns:
        str: The session_id from Geidea (response["session"]["id"]).

    Raises:
        GeideaSessionError: If the API call fails, times out, returns a
                            non-success response code, or returns malformed JSON.
    """
    merchant_ref = order.zoho_books_salesorder_id
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
