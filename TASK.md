# TASK.md — Current Task Description

---

## Task Title

Coupon Module + Order Summary API — Checkout Page Integration

---

## What I Want Done

---

### CONTEXT — Read This First Before Anything

The checkout flow currently works like this:

```
POST /api/shop/orders/checkout/
Bearer Token

Body:
{
  "store_id": 1,
  "address_id": 2,
  "payment_method": "pay_by_link",
  "vat_percent": "5.00",
  "billing_same_as_shipping": true
}
```

This is the ONLY working checkout endpoint. It creates the order directly.
Do NOT break this. Do NOT rewrite this. Read it fully before touching it.

The checkout PAGE in Flutter has:
1. Address Section — existing, working
2. Payment Method Section — existing, working
3. Order Summary — currently shown from cart data on Flutter side

What we are adding:
4. Coupon Section — NEW (backend APIs needed)
5. Order Summary API — NEW (so summary updates dynamically when coupon is applied)

---

### PHASE 1 — Read and Understand Everything First

Before writing any code, do ALL of the following:

**1.1 — Read every existing file (exclude migrations):**
- Read the existing checkout view — understand exactly how the order is created step by step
- Read how VAT is calculated (it must NOT change)
- Read how cart items are fetched for a given store_id + user
- Read how product_id, category_id, collection_id are stored in the cart model
- Read how Zoho API calls are made — find the utility/service file, understand the token pattern
- Read how org_id / store_id maps to Zoho credentials (access token, refresh token)
- Read all existing models — especially Cart, Order, CartItem, Product, and any Address model

**1.2 — Before writing any code, output:**
- What the existing checkout view does step by step (numbered list)
- Where exactly in the checkout flow you will add the coupon hook
- Which file and which function/line you will touch
- Confirm: is there currently a cart_total / subtotal calculation in the checkout? Where?
- Confirm: where is VAT applied? Show the exact calculation
- Confirm: does CartItem have product_id, category_id, collection_id? If not, where are these stored?

**Do not write a single line of code until I confirm your understanding.**

---

### PHASE 2 — Create the `offer` Django App

```bash
python manage.py startapp offer
```

- Register `offer` in `INSTALLED_APPS` in `settings.py`
- Create `offer/urls.py`
- Register offer URLs in root `urls.py` under prefix: `api/offer/`
- Follow the exact same structure, code style, response format, and auth pattern as every other existing app in this project

**File structure to create:**
```
offer/
├── __init__.py
├── admin.py
├── apps.py
├── models.py
├── serializers.py
├── views.py
├── urls.py
├── services.py
├── migrations/
│   └── 0001_initial.py
└── management/
    ├── __init__.py
    └── commands/
        ├── __init__.py
        └── poll_zoho_coupons.py
```

---

### PHASE 3 — Database Models (`offer/models.py`)

#### Model 1: `Coupon`

This is the local copy of all active coupons fetched from Zoho Commerce.
ALL coupon operations in the app run against this table — not Zoho live API.

Rules for `discount_value`:
- Store as CharField to preserve exact format from Zoho
- "10" stays "10" — integer means percentage
- "25.000" stays "25.000" — float means flat amount (use discount_amounts for the value)
- Never normalize: do not convert "10" to "10.00" or "25.000" to "25"

Rules for `expiry_time`:
- Store as DateTimeField, nullable
- null means this coupon has NO expiry — never delete it on schedule
- When expiry_time is set and reached — delete the row

Fields (map directly from Zoho API response):
```
coupon_id (unique), couponset_id, org_id (int, indexed),
coupon_name, coupon_code (indexed), description,
is_active, status, rule_type, coupon_type, show_in_storefront,
restrict_for_guest_user, restrict_for_offline_payments,
stop_after_this_rule, apply_once_per_order, type, duration,
discount_type, discount_by, apply_on,
discount_value (CharField), discount_amounts (JSONField),
max_discount_amount (CharField, blank allowed),
max_redemption (int), max_redemption_count (int),
redemption_count (int), max_redemption_count_per_user (int),
max_usage_per_transaction (int),
max_discounted_product_count_per_cart (CharField, blank allowed),
minimum_order_value (DecimalField 15,3 nullable),
minimum_order_quantity (CharField, blank allowed),
activation_time (DateTimeField nullable),
expiry_at (CharField blank), expiry_time (DateTimeField nullable),
eligible_products (JSONField default=dict),
buy_products (JSONField default=dict),
get_products (JSONField default=dict),
eligible_customers (JSONField default=dict),
eligible_shipping_zones (JSONField default=dict),
raw_data (JSONField),
last_synced_at (auto DateTimeField),
created_at (auto DateTimeField)
```

Add helper method:
```python
def is_expired(self):
    if not self.expiry_time:
        return False
    from django.utils import timezone
    return timezone.now() >= self.expiry_time
```

Meta: `db_table = 'offer_coupon'`, `unique_together = ('coupon_id', 'org_id')`

#### Model 2: `CouponUsageLog`

Source of truth for per-user redemption count.

Fields:
```
user_id (int, indexed), coupon (FK to Coupon, SET_NULL nullable),
coupon_id_str (CharField), coupon_code (CharField),
org_id (int), order_id (int),
discount_amount_applied (DecimalField 15,3),
coupon_type (CharField), discount_type (CharField),
used_at (auto DateTimeField)
```

Meta: `db_table = 'offer_coupon_usage_log'`

After creating models, run:
```bash
python manage.py makemigrations offer
python manage.py migrate
```

---

### PHASE 4 — Polling Job

**File:** `offer/management/commands/poll_zoho_coupons.py`

**Cron schedule:** `0 0,6,12,18 * * * python manage.py poll_zoho_coupons`

**IMPORTANT: Use the existing Zoho token/refresh pattern already in the project. Do not create a new one.**

**Logic — runs for each active organization:**

```
For each org with Zoho credentials:

  Step A — Fetch list:
    GET https://www.zohoapis.in/commerce/v1/coupons
    Header: Authorization: Zoho-oauthtoken {org_access_token}
    Collect all coupon_ids from response

  Step B — Process each coupon in Zoho list:

    If coupon_id NOT in local DB:
      → GET https://www.zohoapis.in/commerce/v1/coupons/{coupon_id}
      → If is_active = true → CREATE row in Coupon table
      → If is_active = false → skip

    If coupon_id ALREADY in local DB:
      → GET https://www.zohoapis.in/commerce/v1/coupons/{coupon_id}
      → If is_active = true → UPDATE all fields in existing row
      → If is_active = false → DELETE the row

  Step C — Cleanup:
    For every Coupon row in DB for this org:
    → If coupon_id not in Zoho response list → DELETE
    → If expiry_time is set AND expiry_time <= now → DELETE
    → Never delete rows where expiry_time is null
```

Must be idempotent. On any Zoho API error: log, skip that org, continue.

---

### PHASE 5 — API 1: List Applicable Coupons

**Endpoint:** `GET /api/offer/checkout-coupons/?store_id=1`

**Auth:** Same Bearer token as all existing endpoints.

**Purpose:** Called when user opens the coupon section in checkout.
Returns only coupons applicable to the user's current cart.
Zero Zoho API calls — from local DB only.

**Step 1 — Safety cleanup:**
```
DELETE any Coupon rows for this org where expiry_time IS SET AND expiry_time <= now
```

**Step 2 — Get cart items:**
Use the existing cart fetch logic for this user + store_id.
For each cart item, get: product_id, category_id, collection_id, quantity, unit_price.
Calculate cart_total = sum of (unit_price * quantity).

**Step 3 — Pre-filter all coupons (applies to every coupon type):**
```
Filter Coupon table: org_id matches AND all of:
1. is_active = true
2. activation_time <= now
3. expiry_time is null OR expiry_time > now
4. If restrict_for_guest_user = true AND user is guest → exclude
5. eligible_customers:
   → apply_to_all_customers = true → include
   → apply_to_all_customers = false → user_id must be in customers list
6. redemption_count < max_redemption_count
7. CouponUsageLog count for (user_id + coupon) < max_redemption_count_per_user
8. If minimum_order_value > 0: cart_total >= minimum_order_value
```

**Step 4 — Type-specific filter:**

```
coupon_type = "transaction":
  Show always if pre-filter passes

coupon_type = "free_shipping":
  Show always if pre-filter passes

coupon_type = "item":
  eligible_products field contains: products[], categories[], collections[]

  If products not empty:
    At least one cart product_id in eligible_products.products → show
  If categories not empty:
    At least one cart category_id in eligible_products.categories → show
  If collections not empty:
    At least one cart collection_id in eligible_products.collections → show
  If all arrays empty:
    Show (applies to all)
  No match in any → exclude

coupon_type = "buyxgety":
  Check buy_products (products[], categories[], collections[], quantity):
    Match cart items using same logic as item coupon above
    Matched item's cart quantity >= buy_products.quantity → else exclude

  Check get_products (products[], quantity):
    get_products.products must exist in cart
    cart quantity of those items >= get_products.quantity → else exclude

  Both must pass → show
```

**Step 5 — Split by rule_type and return:**

```json
{
  "manual_coupons": [
    {
      "coupon_id": "...",
      "coupon_code": "TJ7JJ3USIUI5",
      "coupon_name": "Summer Sale",
      "description": "...",
      "coupon_type": "transaction",
      "rule_type": "manual",
      "discount_type": "flat",
      "discount_value": "25.000",
      "discount_amounts": [...],
      "minimum_order_value": "54.000",
      "max_discount_amount": "",
      "expiry_time": "2026-06-02 00:00"
    }
  ],
  "auto_applied_coupons": [
    { ...same fields, rule_type: "automatic" }
  ]
}
```

---

### PHASE 6 — API 2: Order Summary (Dynamic)

**Endpoint:** `POST /api/offer/order-summary/`

**Auth:** Same as all existing endpoints.

**Purpose:** Called on checkout page load and every time a coupon is applied or removed.
Flutter uses this to show updated totals dynamically.

**Request — without coupon:**
```json
{
  "store_id": 1,
  "vat_percent": "5.00"
}
```

**Request — with coupon:**
```json
{
  "store_id": 1,
  "vat_percent": "5.00",
  "coupon_code": "TJ7JJ3USIUI5"
}
```

**Logic:**

```
Step 1 — Fetch cart items for user + store_id (existing logic)

Step 2 — Base calculation:
  subtotal      = sum(unit_price * quantity)
  vat_amount    = (subtotal * vat_percent) / 100
  shipping      = from existing shipping logic or 0
  base_total    = subtotal + vat_amount + shipping

  VAT is ALWAYS on subtotal before coupon. Do not change this.

Step 3 — If coupon_code provided:

  a. Find in local DB (coupon_code + org_id)
     → Not found → {valid: false, error: "Coupon not found"}

  b. Run all pre-filter checks (same as Phase 5 Step 3)
     → Fail → {valid: false, error: "reason"}

  c. Run type-specific filter (same as Phase 5 Step 4)
     → Not eligible → {valid: false, error: "Coupon not applicable to your cart"}

  d. Calculate discount:

     coupon_type = "transaction":
       if discount_type = "percentage":
         pct = int(discount_value)  ← discount_value is integer string e.g. "10"
         discount = (pct / 100) * subtotal
         if max_discount_amount not empty:
           cap = float(max_discount_amount)
           discount = min(discount, cap)

       if discount_type = "flat":
         ← discount_value is float string e.g. "25.000"
         find matching currency in discount_amounts for this org
         discount = that discount_value from discount_amounts

     coupon_type = "item":
       eligible_total = sum(unit_price * qty) for items matching eligible_products

       if discount_type = "percentage":
         pct = int(discount_value)
         discount = (pct / 100) * eligible_total
         if max_discount_amount not empty:
           discount = min(discount, float(max_discount_amount))

       if discount_type = "flat":
         discount = discount_value from discount_amounts

     coupon_type = "buyxgety":
       get_items_total = sum(unit_price * get_products.quantity)
                         for items matching get_products.products
       pct = int(discount_value)
       discount = (pct / 100) * get_items_total

     coupon_type = "free_shipping":
       discount = shipping amount
       shipping display = "FREE"

  e. final_total = max(0, base_total - discount)
```

**Response — no coupon:**
```json
{
  "coupon_applied": false,
  "subtotal": 1000.00,
  "vat_percent": "5.00",
  "vat_amount": 50.00,
  "shipping_amount": 0.00,
  "coupon_discount": 0.00,
  "total": 1050.00,
  "breakdown": [
    {"label": "Subtotal", "value": 1000.00},
    {"label": "VAT (5%)", "value": 50.00},
    {"label": "Shipping", "value": 0.00},
    {"label": "Total", "value": 1050.00}
  ]
}
```

**Response — with coupon:**
```json
{
  "coupon_applied": true,
  "coupon_code": "TJ7JJ3USIUI5",
  "coupon_name": "Summer Sale",
  "coupon_type": "transaction",
  "subtotal": 1000.00,
  "vat_percent": "5.00",
  "vat_amount": 50.00,
  "shipping_amount": 0.00,
  "coupon_discount": 25.00,
  "total": 1025.00,
  "breakdown": [
    {"label": "Subtotal", "value": 1000.00},
    {"label": "VAT (5%)", "value": 50.00},
    {"label": "Shipping", "value": 0.00},
    {"label": "Coupon Discount (TJ7JJ3USIUI5)", "value": -25.00},
    {"label": "Total", "value": 1025.00}
  ]
}
```

**Response — free shipping coupon:**
```json
{
  "coupon_applied": true,
  "coupon_type": "free_shipping",
  "breakdown": [
    {"label": "Subtotal", "value": 1000.00},
    {"label": "VAT (5%)", "value": 50.00},
    {"label": "Shipping", "value": "FREE"},
    {"label": "Coupon Discount", "value": -50.00},
    {"label": "Total", "value": 1000.00}
  ]
}
```

**Response — invalid coupon:**
```json
{
  "coupon_applied": false,
  "valid": false,
  "error": "This coupon has expired"
}
```

---

### PHASE 7 — Enhance the Existing Checkout API

**Existing endpoint:** `POST /api/shop/orders/checkout/`

**Read this view completely first. State what it does step by step. Wait for confirmation before touching it.**

Add optional coupon fields to the request body:
```json
{
  "store_id": 1,
  "address_id": 2,
  "payment_method": "pay_by_link",
  "vat_percent": "5.00",
  "billing_same_as_shipping": true,
  "coupon_code": "TJ7JJ3USIUI5",
  "coupon_discount": 25.00
}
```

`coupon_code` and `coupon_discount` are both optional.
When absent: existing logic runs exactly as before. Zero change.

When `coupon_code` is present, add this flow:

```
BEFORE order creation:

1. Find coupon in local DB by coupon_code + org_id
   → Not found → 400: {"error": "Coupon not found"}

2. Fetch LIVE from Zoho: GET /commerce/v1/coupons/{coupon_id}
   (1 API call — this is the final check)

3. Check: live redemption_count < live max_redemption_count
   → Reached → 400:
   {"error": "Sorry, this coupon is no longer available. Please place your order without it."}

4. Pass → proceed with existing order creation
   Apply coupon_discount to reduce the order total
   (use the coupon_discount value from the request — do not recalculate)

AFTER order successfully created:

5. PATCH Zoho: update redemption_count = live_redemption_count + 1
   → If PATCH fails: log error, do NOT fail the order

6. Update local DB: coupon.redemption_count += 1, save()

7. Create CouponUsageLog record:
   user_id, coupon, coupon_id_str, coupon_code, org_id,
   order_id (new order), discount_amount_applied (from request),
   coupon_type, discount_type, used_at
```

**Transaction safety:**
- Steps 1-3 happen BEFORE order creation (gate)
- Steps 5-7 happen AFTER order creation succeeds
- If order creation fails → skip steps 5-7 entirely
- If Zoho PATCH fails → log it, order stands

---

### PHASE 8 — Endpoints Summary

| Method | Endpoint | Purpose | Zoho calls |
|--------|----------|---------|------------|
| GET | `/api/offer/checkout-coupons/?store_id=1` | Applicable coupons for cart | 0 |
| POST | `/api/offer/order-summary/` | Dynamic order total | 0 |
| POST | `/api/shop/orders/checkout/` (enhanced) | Place order with optional coupon | 0 or 1 |
| Command | `poll_zoho_coupons` | Sync coupons from Zoho | 4×/day |

---

## Why This Task

Checkout is live. Client wants coupon support in the checkout flow. Coupons exist in Zoho Commerce. We need to fetch and store them locally, display the right ones per cart, calculate discounts correctly, and sync redemption counts back to Zoho after successful orders.

---

## Pre-conditions — Must Be True Before Starting

- [ ] `POST /api/shop/orders/checkout/` is working
- [ ] Cart model exists with product_id, quantity, unit_price per cart item
- [ ] Zoho Commerce API integration working (token refresh, per-org credentials)
- [ ] VAT calculation working in checkout — must NOT change
- [ ] 3 organizations exist with Zoho credentials in DB

---

## What "Done" Looks Like

- `offer` app created, migrations run without errors
- `python manage.py poll_zoho_coupons` runs, stores correct coupons per org
- `GET /api/offer/checkout-coupons/?store_id=1` returns correct filtered coupons
- `POST /api/offer/order-summary/` returns correct breakdown with and without coupon
- `POST /api/shop/orders/checkout/` works identically to before when no coupon sent
- When coupon sent: live check runs, order created, Zoho patched, log created
- VAT unchanged in all scenarios
- No existing endpoint broken
- `CouponUsageLog` record for every confirmed coupon usage

---

## Out of Scope — Do NOT Touch

- Any existing auth logic
- Any existing VAT calculation
- Any existing cart or wishlist logic
- Any existing payment method logic
- Any product or category listing APIs
- Banner images or Best Deals section
- Flutter/frontend code
- README.md (agent asks at end)

---

## Files or Screens Involved

**New:**
- `offer/` entire app
- `offer/models.py` — Coupon, CouponUsageLog
- `offer/views.py` — checkout-coupons, order-summary
- `offer/services.py` — all business logic
- `offer/serializers.py`
- `offer/urls.py`
- `offer/management/commands/poll_zoho_coupons.py`

**Existing (read fully before touching):**
- `settings.py` — add offer to INSTALLED_APPS
- Root `urls.py` — add offer prefix
- Existing checkout view — add coupon hook only (state what you're changing first)

---

## Anything the Agent Should Be Careful About

- **Read the existing checkout view completely before Phase 7. State what you found. Wait for confirmation.**
- **discount_value is stored as CharField.** Integer "10" stays "10". Float "25.000" stays "25.000". Never normalize.
- **VAT is on subtotal before coupon discount.** Coupon line appears after VAT. Never move VAT.
- **Do not recalculate coupon_discount in Phase 7.** Trust the value from the request. Only do the live redemption check.
- **Polling is per org.** Each org has its own access token. Use existing token utility.
- **Coupons with null expiry_time are never deleted.**
- **CouponUsageLog is the source of truth for per-user usage count.** Always query this, never rely on Zoho for per-user tracking.
- **If Zoho PATCH fails after order** — log it, do not fail the order.
- **checkout-coupons endpoint makes zero Zoho API calls.**
- **Auto coupons (rule_type = automatic)** go in `auto_applied_coupons` array in response.
- **Free shipping** — discount equals the shipping charge. Breakdown shows "Shipping: FREE".

---

*Paste into Copilot Agent in this order: AGENT_STARTER_PROMPT.md → PROJECT.md → README.md → this TASK.md. Wait for the agent to confirm understanding before any code is written.*
