# Geidea Payment Gateway — Remaining Phases Master Implementation Prompt

> **This document covers:** Phase 3 (Callback), Phase 4 (Status Check), Stale Order Cleanup
>
> **Environment confirmed:** UAE — `api.geidea.ae` — currency `AED`
>
> **Phases already complete:** Phase 1 (Checkout) ✅ Phase 2 (Initiate Session) ✅

---

## HOW TO USE THIS DOCUMENT

This prompt is designed to be executed **one phase at a time**. The developer must
follow this exact sequence for every phase:

```
STEP 1 — Read and understand the phase section completely before writing any code
STEP 2 — Cross-check the "Known Facts from Completed Phases" section below
STEP 3 — Implement the phase exactly as specified
STEP 4 — Stop. Do not proceed to the next phase.
STEP 5 — Run the test cases provided at the end of each phase section
STEP 6 — Share test results with the user
STEP 7 — Wait for user to confirm "everything is okay"
STEP 8 — Create a completion notes file for that phase
STEP 9 — Only when user says "start next phase" — proceed to the next section
```

**Do not implement Phase 4 while implementing Phase 3.**
**Do not implement Stale Cleanup while implementing Phase 4.**
One phase. Stop. Confirm. Then continue.

---

## KNOWN FACTS FROM COMPLETED PHASES — READ BEFORE ANY PHASE

These are confirmed from actual implementation. Every phase must respect these.

### Model Facts (confirmed from shop/models.py)
- `Order.PaymentStatus` has exactly three values: `PENDING`, `PAID`, `NOT_REQUIRED`
- `Order.PaymentStatus` has **NO** `CANCELLED` value
- Cancellation is on a separate field: `Order.Status.CANCELLED`
- Any cancelled order check must use `order.status == Order.Status.CANCELLED`
- `order.zoho_books_salesorder_id` — CharField, stores Zoho Books Sales Order ID
- `order.gateway_reference` — CharField, will store Geidea's UUID after payment
- `order.payment_status` — uses `Order.PaymentStatus` enum

### Environment Facts (confirmed from testing)
- Environment: **UAE**
- Base URL: `https://api.geidea.ae`
- Currency: `AED`
- Settings keys: `GEIDEA_PUBLIC_KEY`, `GEIDEA_API_PASSWORD`, `GEIDEA_SESSION_URL`, `GEIDEA_CALLBACK_URL`
- All four are already in `settings.py` and `.env`

### File Facts (confirmed from Phase 2 implementation)
- `shop/services/geidea.py` exists — contains `GeideaSessionError`, `create_geidea_session()`
- `GeideaInitiateView` exists in `shop/views.py` at lines 2788–2871
- `geidea/initiate/` URL is registered in `shop/urls.py`

### Open Item — merchantReferenceId Format
Geidea docs say `merchantReferenceId` must be a valid UUID.
Current implementation sends `zoho_books_salesorder_id` (e.g. `"SO-00042"`) which is not a UUID.
Phase 2 sandbox test passed anyway — but this must be resolved in Phase 3.

**Resolution chosen: Option B — new `geidea_merchant_ref` UUID field on Order model.**
- Add `geidea_merchant_ref = models.UUIDField(null=True, blank=True)` to Order
- Generate on first `/initiate/` call and store it
- Use as `merchantReferenceId` for all Geidea calls
- Zoho SO ID is still stored on `zoho_books_salesorder_id` for business traceability
- Requires one migration — do this as part of Phase 3 pre-work

> ⚠️ If the team decides to use Option A instead (deterministic UUID from order.pk,
> no migration), replace all references to `order.geidea_merchant_ref` in this
> document with `str(uuid.uuid5(uuid.NAMESPACE_DNS, str(order.pk)))`.

### Signature Algorithm Facts (confirmed from Geidea docs)
```
CREATE SESSION (Phase 2 — already working):
  concat    = PublicKey + format(amount,'0.2f') + Currency + merchantReferenceId + timestamp
  signature = Base64( HMAC-SHA256( key=APIPassword, msg=concat ) )

CALLBACK VERIFICATION (Phase 3 — different fields):
  concat    = PublicKey + format(amount,'0.2f') + Currency + geidea_orderId + Status + merchantReferenceId + timestamp
  expected  = Base64( HMAC-SHA256( key=APIPassword, msg=concat ) )
  compare   expected == payload["signature"]
```

> ⚠️ **Callback timestamp open item:** The exact field name for `timestamp` in the
> callback payload is unconfirmed. The first sandbox callback must be logged raw
> to determine if it is `"timestamp"`, `order["createdDate"]`, or another field.
> Build Phase 3 to log the full raw body on the first hit so this can be confirmed.

---

## PRE-WORK BEFORE PHASE 3 — Model Migration

Before writing any Phase 3 view code, do this first:

### Step 1 — Add field to Order model in `shop/models.py`

Find the Order model and add this field alongside `gateway_reference`:

```python
geidea_merchant_ref = models.UUIDField(
    null=True,
    blank=True,
    help_text="UUID sent to Geidea as merchantReferenceId. Generated on first payment initiation."
)
```

### Step 2 — Create and run migration

```bash
python manage.py makemigrations shop --name="add_geidea_merchant_ref_to_order"
python manage.py migrate
```

### Step 3 — Update `create_geidea_session()` in `shop/services/geidea.py`

Find the line:
```python
merchant_ref = order.zoho_books_salesorder_id
```

Replace it with:
```python
import uuid as uuid_module

# Generate a UUID for Geidea if not already set.
# Geidea requires merchantReferenceId to be a valid UUID.
# We store it on the order so the callback can look it up.
if not order.geidea_merchant_ref:
    order.geidea_merchant_ref = uuid_module.uuid4()
    order.save(update_fields=['geidea_merchant_ref'])

merchant_ref = str(order.geidea_merchant_ref)
```

### Step 4 — Update `GeideaInitiateView` in `shop/views.py`

No changes needed in the view itself — the service function now handles the UUID
generation and storage automatically.

### Step 5 — Verify in shell before proceeding

```python
from shop.models import Order
order = Order.objects.get(pk=119)  # use your test order
print(order.geidea_merchant_ref)   # should be None before first initiate call

# Call initiate again via Postman, then:
order.refresh_from_db()
print(order.geidea_merchant_ref)   # should now be a UUID
```

Once this is confirmed working, proceed to Phase 3 implementation.

---

## PHASE 3 — GEIDEA CALLBACK

### What This Phase Does

Geidea POSTs the payment result to your server after the user completes (or fails)
payment on the HPP. This endpoint receives that callback, verifies it is genuine,
and if the payment succeeded, marks the order as paid.

This is the most security-critical endpoint in the entire integration.
Get it right before anything else.

### New Endpoint

```
POST /api/shop/geidea/callback/
Auth: NONE — open endpoint, secured by signature verification only
```

### New File — `shop/services/geidea_callback.py`

Create this file from scratch. Do not add to `geidea.py` — keep callback logic separate.

```python
import base64
import hashlib
import hmac
import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction

from shop.models import Order
from shop.services.credit import credit_user_for_prepaid_order
from shop.services.zoho_books_payment import maybe_create_zoho_books_advance_payment_for_order

logger = logging.getLogger(__name__)


def verify_callback_signature(payload):
    """
    Verify the HMAC-SHA256 signature in the Geidea callback payload.

    Callback signature concatenation is DIFFERENT from create-session:
      create-session: PublicKey + amount + currency + merchantRef + timestamp
      callback:       PublicKey + amount + currency + geideaOrderId + status + merchantRef + timestamp

    Returns True if signature is valid, False otherwise.

    ⚠️ OPEN ITEM — timestamp field:
    The Geidea callback signature formula includes a timestamp.
    The exact field name is not confirmed — could be top-level "timestamp"
    or order["createdDate"]. This function attempts "timestamp" first.
    Log the full raw body on the first sandbox callback to confirm.
    """
    try:
        order_data      = payload["order"]
        received_sig    = payload["signature"]
        merchant_pub_key = order_data["merchantPublicKey"]
        amount_str      = f"{float(order_data['totalAmount']):.2f}"
        currency        = order_data["currency"]
        geidea_order_id = order_data["orderId"]
        status          = order_data["status"]
        merchant_ref    = order_data["merchantReferenceId"]

        # ⚠️ Try top-level timestamp first.
        # If signature verification keeps failing after confirming other fields,
        # switch to order_data.get("createdDate", "") or check the raw callback log.
        timestamp = payload.get("timestamp", "")

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
    logger.info("Geidea callback received. payload=%s", payload)

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

            # Credit the user — this sets payment_status=PAID and gateway_reference
            credit_user_for_prepaid_order(
                order,
                amount=Decimal(str(callback_amount)),
                gateway_reference=geidea_order_id,  # Geidea's UUID
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

    # ── Step D: Zoho Advance Payment (outside atomic block) ───────────────
    # Order is now PAID. Zoho failure must never reverse this.
    try:
        maybe_create_zoho_books_advance_payment_for_order(order)
    except Exception as exc:
        logger.critical(
            "Geidea callback — Zoho advance payment failed AFTER order marked PAID. "
            "order_pk=%s zoho_so=%s amount=%s error=%s "
            "ACTION REQUIRED: reconcile manually via OrderZohoBooksPaymentAPIView.",
            order.pk, order.zoho_books_salesorder_id, callback_amount, exc,
        )
        # Still return 200 to Geidea — order is paid, Zoho is a separate concern

    return 200, "OK"
```

### New View — Add to `shop/views.py`

Add this class alongside `GeideaInitiateView`. Add the import at the top of views.py:

```python
from shop.services.geidea_callback import process_geidea_callback
```

Add the view class:

```python
class GeideaCallbackView(APIView):
    """
    POST /api/shop/geidea/callback/
    Open endpoint — no JWT auth. Secured by HMAC signature only.

    Geidea POSTs payment results here after the user completes or
    abandons payment on the HPP. This is the authoritative payment confirmation.

    Always returns HTTP 200 to Geidea except on signature mismatch (400).
    Geidea retries on non-200 — returning 200 on failures prevents retry loops.
    """
    permission_classes = []   # No auth — open endpoint
    authentication_classes = []  # No JWT parsing

    def post(self, request):
        try:
            payload = request.data
        except Exception:
            return Response({"message": "Invalid payload"}, status=400)

        http_status, message = process_geidea_callback(payload)
        return Response({"message": message}, status=http_status)
```

### URL — Add to `shop/urls.py`

```python
from shop.views import GeideaCallbackView

path('geidea/callback/', GeideaCallbackView.as_view(), name='geidea-callback'),
```

### CSRF — Important

The callback endpoint receives POST requests from Geidea's servers, not from a browser.
Django's CSRF middleware will block it. Ensure one of these is true:

**Option 1 (recommended):** The callback URL is already under an API router that
has `CsrfExemptSessionAuthentication` or your project uses `DEFAULT_AUTHENTICATION_CLASSES`
without CSRF for API views. Since `authentication_classes = []` is set, DRF should
skip CSRF. Verify this works in testing.

**Option 2 (explicit):** If CSRF errors appear in logs, import and apply:
```python
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

@method_decorator(csrf_exempt, name='dispatch')
class GeideaCallbackView(APIView):
    ...
```

Only apply if actually needed — do not add it preemptively.

---

### Phase 3 — Test Cases

After implementation, run every one of these before telling the user it is complete.

**Test Case 3.1 — Valid successful callback (happy path)**

Simulate a callback POST to `/api/shop/geidea/callback/` with a payload where:
- `order.status = "Success"`, `order.detailedStatus = "Paid"`
- `order.totalAmount` matches the Django order total
- `order.currency = "AED"`
- `order.merchantReferenceId` = the UUID stored in `order.geidea_merchant_ref`
- `signature` is correctly computed

Expected:
- HTTP 200
- `order.payment_status` changes to `PAID`
- `order.gateway_reference` = the `orderId` from callback payload
- `AccountCreditLedger` record created
- `maybe_create_zoho_books_advance_payment_for_order()` called

**Test Case 3.2 — Signature mismatch**

Send a callback with a tampered `signature` field.

Expected:
- HTTP 400
- Order NOT updated
- Security alert logged

**Test Case 3.3 — Payment failed (status != "Success")**

Send callback with `order.status = "Failed"`, `order.detailedStatus = "Failed"`.

Expected:
- HTTP 200 (Geidea must not retry)
- Order stays `PENDING`

**Test Case 3.4 — Duplicate callback (idempotency)**

Send the same successful callback twice.

Expected:
- First call: HTTP 200, order marked PAID
- Second call: HTTP 200, order NOT modified (already PAID check triggers)

**Test Case 3.5 — Amount mismatch (tampered amount)**

Send a callback with `order.totalAmount` different from the Django order's total.

Expected:
- HTTP 400
- Security alert logged
- Order NOT updated

**Test Case 3.6 — Unknown merchantReferenceId**

Send a callback with a `merchantReferenceId` that doesn't match any `geidea_merchant_ref`.

Expected:
- HTTP 200 (Geidea must not retry)
- CRITICAL log entry

**Test Case 3.7 — Real sandbox end-to-end**

Use the frontend (or Geidea's test page) to complete a real payment with a test card.
Check webhook.site to confirm the callback arrived.
Check Django logs to confirm signature verification and order update.

Expected:
- Callback received at webhook.site (confirms callbackUrl is correct)
- Django log shows "order marked PAID"
- `order.payment_status = PAID` in DB

> ⚠️ **Log the full raw callback payload from webhook.site.**
> Extract the `timestamp` field or determine if `createdDate` is used in the signature.
> Update `verify_callback_signature()` accordingly before going live.

---

**Provide test results to user. Wait for confirmation. Then create Phase 3 completion notes.**

---

## PHASE 4 — STATUS CHECK ENDPOINT

> **Only start this after user confirms Phase 3 is complete and working.**

### What This Phase Does

Two things:
1. A `GET /api/shop/geidea/status/` endpoint the frontend calls if polling times out
2. A `fetch_geidea_orders_by_merchant_ref()` function added to `shop/services/geidea.py`
   that is reused by both this endpoint and the Stale Cleanup job

### New Function — Add to `shop/services/geidea.py`

Add this function below `create_geidea_session()`:

```python
# UAE fetch URL — adjust if environment changes
GEIDEA_FETCH_URL = "https://api.geidea.ae/pgw/api/v1/direct/order"


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
            GEIDEA_FETCH_URL,
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
```

Also add the fetch URL to `settings.py` and `.env`:

```env
# .env
GEIDEA_FETCH_URL=https://api.geidea.ae/pgw/api/v1/direct/order
```

```python
# settings.py
GEIDEA_FETCH_URL = os.environ.get('GEIDEA_FETCH_URL', '')
```

Then update the function to use `settings.GEIDEA_FETCH_URL` instead of the hardcoded URL.

### New View — Add to `shop/views.py`

Add import:

```python
from shop.services.geidea import fetch_geidea_orders_by_merchant_ref
```

Add view class:

```python
class GeideaStatusView(APIView):
    """
    GET /api/shop/geidea/status/?order_id=<pk>

    Manual fallback called by frontend if polling times out.
    Fetches the order status directly from Geidea and reconciles
    if payment succeeded but callback was missed.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        order_id = request.query_params.get('order_id')
        if not order_id:
            return Response(
                {'error': 'order_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            order = Order.objects.get(pk=order_id, user=request.user)
        except Order.DoesNotExist:
            return Response(
                {'error': 'Order not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Already paid — no need to check Geidea
        if order.payment_status == Order.PaymentStatus.PAID:
            return Response({'status': 'paid'})

        # No Geidea merchant ref means initiate was never called successfully
        if not order.geidea_merchant_ref:
            return Response({'status': 'pending'})

        # Fetch from Geidea
        orders_list = fetch_geidea_orders_by_merchant_ref(order.geidea_merchant_ref)

        paid_entry = next(
            (o for o in orders_list
             if o.get("status") == "Success" and o.get("detailedStatus") == "Paid"),
            None
        )

        if paid_entry:
            # Callback was missed — reconcile manually
            from decimal import Decimal
            from shop.services.geidea_callback import process_geidea_callback

            logger.warning(
                "GeideaStatusView — missed callback detected. "
                "order_pk=%s geidea_order_id=%s Reconciling manually.",
                order.pk, paid_entry.get("orderId"),
            )
            try:
                with transaction.atomic():
                    order = Order.objects.select_for_update().get(pk=order.pk)
                    if order.payment_status != Order.PaymentStatus.PAID:
                        credit_user_for_prepaid_order(
                            order,
                            amount=Decimal(str(paid_entry["totalAmount"])),
                            gateway_reference=paid_entry["orderId"],
                        )
                maybe_create_zoho_books_advance_payment_for_order(order)
            except Exception as exc:
                logger.error(
                    "GeideaStatusView — reconciliation failed. order_pk=%s error=%s",
                    order.pk, exc,
                )
                return Response({'status': 'pending'})

            return Response({'status': 'paid'})

        return Response({'status': 'pending'})
```

Add these imports to views.py if not already present:
```python
from django.db import transaction
from shop.services.credit import credit_user_for_prepaid_order
from shop.services.zoho_books_payment import maybe_create_zoho_books_advance_payment_for_order
```

### URL — Add to `shop/urls.py`

```python
from shop.views import GeideaStatusView

path('geidea/status/', GeideaStatusView.as_view(), name='geidea-status'),
```

---

### Phase 4 — Test Cases

**Test Case 4.1 — Order already paid**

Call `GET /api/shop/geidea/status/?order_id=<paid_order_id>` on an order
whose `payment_status` is already `PAID`.

Expected:
- HTTP 200 `{ "status": "paid" }`
- No Geidea API call made

**Test Case 4.2 — Order pending, no geidea_merchant_ref**

Call status on an order that was created but `/initiate/` was never called.

Expected:
- HTTP 200 `{ "status": "pending" }`

**Test Case 4.3 — Order pending, Geidea has no paid entry**

Call status on an order where Geidea fetch returns no `Success` entries.

Expected:
- HTTP 200 `{ "status": "pending" }`

**Test Case 4.4 — Missed callback recovery**

Manually set an order's `payment_status` back to `PENDING` in the DB (simulate missed callback).
Geidea should have a paid record for it.
Call `GET /api/shop/geidea/status/?order_id=<pk>`.

Expected:
- HTTP 200 `{ "status": "paid" }`
- `order.payment_status` = `PAID` in DB
- `AccountCreditLedger` record created
- Warning logged: "missed callback detected"

**Test Case 4.5 — Missing order_id param**

Call `GET /api/shop/geidea/status/` with no query param.

Expected:
- HTTP 400 `{ "error": "order_id is required." }`

**Test Case 4.6 — Wrong user**

Call status with a valid `order_id` that belongs to a different user.

Expected:
- HTTP 404

---

**Provide test results to user. Wait for confirmation. Then create Phase 4 completion notes.**

---

## STALE ORDER CLEANUP — APSCHEDULER JOB

> **Only start this after user confirms Phase 4 is complete and working.**

### What This Does

A background job that runs every hour and finds `payment_gateway` orders that are
still `PENDING` after 2 hours. For each one it checks Geidea — if payment was
made it reconciles, if not it cancels the order.

### New Function — Add to `shop/services/geidea.py`

```python
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
    from shop.services.credit import credit_user_for_prepaid_order
    from shop.services.zoho_books_payment import (
        maybe_create_zoho_books_advance_payment_for_order,
        void_zoho_books_sales_order_for_order,
    )
~
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
    from shop.services.zoho_books_payment import void_zoho_books_sales_order_for_order
    try:
        # Use your existing order cancellation transition function
        apply_order_sync_transition(order, 'cancelled')
        void_zoho_books_sales_order_for_order(order)
    except Exception as exc:
        logger.error(
            "Stale order cleanup — cancellation failed. order_pk=%s error=%s",
            order.pk, exc,
        )
```

> ⚠️ Replace `apply_order_sync_transition(order, 'cancelled')` with the exact
> function and argument your codebase uses for cancelling an order.
> Check your existing stale order cleanup code or order management service
> for the correct call signature.

### Scheduler Job — Add to Your Existing APScheduler Setup

Find your existing APScheduler job registration file (wherever your other scheduled
jobs are registered) and add:

```python
from shop.models import Order
from shop.services.geidea import reconcile_or_cancel_stale_order
from django.utils import timezone
from datetime import timedelta

def cleanup_stale_payment_gateway_orders():
    """
    Runs every hour.
    Finds payment_gateway orders that are still PENDING after 2 hours
    and reconciles or cancels them.
    """
    cutoff = timezone.now() - timedelta(hours=2)
    stale_orders = Order.objects.filter(
        payment_method=Order.PaymentMethod.PAYMENT_GATEWAY,
        payment_status=Order.PaymentStatus.PENDING,
        created_at__lt=cutoff,
    )

    logger.info(
        "Stale order cleanup — found %d stale orders.", stale_orders.count()
    )

    for order in stale_orders:
        reconcile_or_cancel_stale_order(order)


# Register in your scheduler:
scheduler.add_job(
    cleanup_stale_payment_gateway_orders,
    'interval',
    hours=1,
    id='cleanup_stale_payment_gateway_orders',
    replace_existing=True,
)
```

---

### Stale Cleanup — Test Cases

**Test Case SC.1 — Order with no geidea_merchant_ref**

Create a stale `payment_gateway` order with `geidea_merchant_ref = null` (initiate
was never called). Run the cleanup job.

Expected:
- Order cancelled
- Zoho SO voided
- No Geidea API call made

**Test Case SC.2 — Stale order, Geidea has successful payment (missed callback)**

Create a stale order where Geidea fetch returns a `Success/Paid` entry.

Expected:
- Order marked PAID
- `AccountCreditLedger` created
- `maybe_create_zoho_books_advance_payment_for_order()` called
- Warning "missed callback detected" in logs

**Test Case SC.3 — Stale order, Geidea has only failed entries**

Create a stale order where Geidea returns entries but all are `Failed`.

Expected:
- Order cancelled
- Zoho SO voided

**Test Case SC.4 — Stale order, Geidea returns empty array**

Create a stale order where Geidea returns `{ "orders": [] }`.

Expected:
- Order cancelled
- Zoho SO voided

**Test Case SC.5 — Stale order already PAID before job runs**

If the callback arrives between the job finding the order and processing it,
`select_for_update()` inside the reconcile function handles it via the
`payment_status != PAID` check.

Expected:
- No duplicate credit
- No crash
- Log shows order was already paid

**Test Case SC.6 — Run job manually to verify it triggers**

```python
from shop.tasks import cleanup_stale_payment_gateway_orders
cleanup_stale_payment_gateway_orders()
```

Check logs confirm it ran and found/processed orders correctly.

---

**Provide test results to user. Wait for confirmation. Then create Stale Cleanup completion notes.**

---

## FINAL VERIFICATION — END-TO-END FLOW

After all phases and stale cleanup are confirmed working individually,
run this complete end-to-end test:

```
1. POST /api/shop/orders/checkout/
   → Order created, payment_status = PENDING, zoho_books_salesorder_id populated

2. POST /api/shop/geidea/initiate/
   → session_id returned, order.geidea_merchant_ref now populated in DB

3. Use test card on Geidea HPP (or Geidea test page)
   → Payment completes

4. Verify callback received at GEIDEA_CALLBACK_URL
   → Check webhook.site or your live server logs
   → Confirm order.payment_status = PAID in DB
   → Confirm order.gateway_reference = Geidea's orderId UUID
   → Confirm AccountCreditLedger record created
   → Confirm Zoho advance payment created

5. Poll GET /api/shop/orders/<pk>/
   → payment_status = "paid" returned ✅

6. Attempt to call POST /api/shop/geidea/initiate/ again on same order
   → HTTP 400 "Order already paid" ✅

7. Call GET /api/shop/geidea/status/?order_id=<pk>
   → { "status": "paid" } ✅
```

Only when all 7 steps pass is the integration considered complete and ready for production.

---

## REFERENCE — ALL GEIDEA ENDPOINTS USED

| Purpose | Method | URL |
|---|---|---|
| Create Session | POST | `https://api.geidea.ae/payment-intent/api/v2/direct/session` |
| Fetch by MerchantRef | GET | `https://api.geidea.ae/pgw/api/v1/direct/order?MerchantReferenceId={uuid}` |
| HPP JS Library | Script | `https://payments.geidea.ae/hpp/geideaCheckout.min.js` |

## REFERENCE — ALL YOUR ENDPOINTS

| Phase | Method | URL | Auth |
|---|---|---|---|
| Phase 1 | POST | `/api/shop/orders/checkout/` | JWT |
| Phase 2 | POST | `/api/shop/geidea/initiate/` | JWT |
| Phase 3 | POST | `/api/shop/geidea/callback/` | None (signature) |
| Phase 4 | GET | `/api/shop/geidea/status/?order_id=<pk>` | JWT |