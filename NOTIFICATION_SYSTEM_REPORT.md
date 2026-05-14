# AoneGT Notification System Technical Report

## Scope

This report summarizes the Django REST backend as it exists in this workspace, with emphasis on multi-organization Zoho handling, coupon synchronization, authentication, polling, and notification-related infrastructure. It is written for an external AI assistant that needs a reliable backend map before designing a notification system.

Where the Flutter app is concerned, this workspace does not contain the mobile source tree, so frontend-specific claims are only stated when they are directly implied by backend behavior.

## 1. Multi-Org Architecture

### Database representation of Zoho orgs

There are two backend models that represent Zoho organization context:

1. `catalog.Store`
   - This is the main operational tenant-like record for store-scoped backend behavior.
   - Relevant fields:
     - `slug` - unique local identifier for the store.
     - `zoho_org_id` - Zoho Commerce organization id used for Commerce admin calls.
     - `zoho_store_domain` - storefront domain used for storefront API calls.
     - `client_id`, `client_secret`, `refresh_token` - optional per-store OAuth credentials.
     - `access_token`, `token_expiry` - cached per-store token state.
     - `is_active`, `sort_order`, `created_at`, and descriptive metadata such as `name`, `category`, `contact_email`.

2. `zoho_integration.ZohoCommerceAccount`
   - This is a Zoho account credential container for the multi-account Zoho integration module.
   - Relevant fields:
     - `name`
     - `email` - unique.
     - `organization_id` - Zoho org id.
     - `accounts_url`
     - `commerce_base_url`
     - `client_id`, `client_secret`, `refresh_token`
     - `is_active`, `created_at`

### Tenant/org model

There is no dedicated `Tenant` model. In practice, `catalog.Store` is the closest tenant boundary for storefront, cart, order, coupon, and notification scoping. The `Store` model carries the Zoho organization and storefront identity used by most runtime flows.

### Unique organization identifiers

The backend uses multiple identifiers depending on context:

- `Store.slug` is the unique local store identifier.
- `Store.zoho_org_id` is the Zoho Commerce organization identifier used in API calls and coupon scoping.
- `ZohoCommerceAccount.organization_id` is the org id for the multi-account Zoho integration layer.
- `ZohoCommerceAccount.email` is unique, but it is not the org key.

### Coupon scoping per org

Yes. Coupons are scoped per organization through `offer.Coupon.org_id`.

- The model defines `unique_together = ('coupon_id', 'org_id')`.
- All coupon lookup and sync helpers filter by `org_id`.
- `coupon_id` alone is not treated as globally unique; the org boundary is part of the natural key.

## 2. Zoho Authentication

### Authentication method

The project uses OAuth2 refresh-token flow for Zoho Commerce and Zoho multi-account access. There is no API-key-based integration in the scanned code.

There are two important variants:

1. Per-store / per-account OAuth refresh
   - `shop.services.zoho_commerce.ZohoCommerceService.refresh_access_token()` refreshes an access token using refresh token, client id, and client secret.
   - `zoho_integration.services.get_zoho_access_token(account)` does the same for a `ZohoCommerceAccount` row and keeps a short-lived in-memory token cache.

2. Static token env-based checks
   - The registration-gate helpers in `accounts.services.zoho_inventory_contact` and `accounts.services.zoho_commerce_contact` use `ZOHO_ACCESS_TOKEN` plus an organization id from environment variables.
   - Those helpers are for registration email checks, not the main coupon sync pipeline.

### Credential storage

Credentials are stored in three places:

- Environment variables for global or gate-style usage:
  - `ZOHO_ACCESS_TOKEN`
  - `ZOHO_REFRESH_TOKEN`
  - `ZOHO_CLIENT_ID`
  - `ZOHO_CLIENT_SECRET`
  - `ZOHO_ORG_ID`
  - `ZOHO_COMMERCE_ORGANIZATION_ID`
  - `ZOHO_INVENTORY_ORGANIZATION_ID`
  - `ZOHO_ACCOUNTS_URL`
  - `ZOHO_COMMERCE_BASE_URL`
  - `ZOHO_STORE_DOMAIN`

- `catalog.Store` database rows:
  - `client_id`, `client_secret`, `refresh_token`
  - `access_token`, `token_expiry`
  - `zoho_org_id`, `zoho_store_domain`

- `zoho_integration.ZohoCommerceAccount` database rows:
  - `client_id`, `client_secret`, `refresh_token`
  - `organization_id`, `accounts_url`, `commerce_base_url`

### Token model

There is no dedicated Zoho token model.

What exists instead:

- `Store.access_token` and `Store.token_expiry` persist a reusable token for store-scoped Commerce helpers.
- `zoho_integration.services` keeps an in-memory `_TOKEN_CACHE` keyed by account id or email.
- `ZohoCommerceAccount` does not store an access token field.

### Token refresh handling

Token refresh is handled by utility functions, not by middleware and not by Celery:

- `shop.services.zoho_commerce.ZohoCommerceService.refresh_access_token()`
- `zoho_integration.services.get_zoho_access_token()`
- `accounts.services.zoho_inventory_contact` and `accounts.services.zoho_commerce_contact` for static-token contact checks

### Single master token vs independent org auth

The system supports both patterns:

- Independent org/account auth exists through `ZohoCommerceAccount` and per-store `Store` credentials.
- A single global fallback token path also exists through environment variables for shop helpers and registration checks.

So the architecture is mixed: multi-org capable, but not purely per-tenant isolated everywhere.

## 3. Zoho Commerce Integration

### Coupon endpoints being called

The coupon sync logic in `offer.services` uses these Zoho Commerce endpoints:

- `GET /commerce/v1/coupons`
- `GET /commerce/v1/coupons/{coupon_id}`
- `PATCH /commerce/v1/coupons/{coupon_id}`

Requests are sent against the base host from `ZOHO_API_BASE_HOST`, defaulting to `https://www.zohoapis.com`.

All admin calls include:

- `Authorization: Zoho-oauthtoken <token>`
- `X-com-zoho-store-organizationid: <org_id>`

### API library / wrapper

The project uses the Python `requests` library for the coupon sync flow.

Relevant helpers:

- `offer.services.sync_zoho_coupons_for_store()`
- `offer.services.sync_coupon_from_payload()`
- `offer.services.get_live_coupon_for_checkout()`
- `shop.services.zoho_commerce.ZohoCommerceService.admin_headers()`

The broader Zoho Commerce helper module is `shop/services/zoho_commerce.py`. It provides:

- `ZohoCommerceService.refresh_access_token()`
- `ZohoCommerceService.admin_headers()`
- `ZohoCommerceService.storefront_headers()`
- `ZohoCommerceService.get_products_storefront()`
- `ZohoCommerceService.get_product_detail_storefront()`
- low-level helpers such as `commerce_store_request()`, `commerce_store_get()`, and `commerce_store_post()`

### Coupon response shape

The code handles multiple Zoho response envelopes rather than one fixed schema.

For the list endpoint, the code accepts coupon arrays under:

- `coupons`
- `data`
- `items`

For the detail endpoint, the code accepts:

- `coupon`
- `data`
- or the raw dict itself if the response is already flattened

Key fields read or normalized by the sync logic include:

- `coupon_id` or `id`
- `couponset_id` / `coupon_set_id`
- `coupon_code` / `code`
- `coupon_name` / `name`
- `description`
- `is_active`
- `status`
- `rule_type`
- `coupon_type` / `type`
- `show_in_storefront`
- `restrict_for_guest_user`
- `restrict_for_offline_payments`
- `stop_after_this_rule`
- `apply_once_per_order`
- `duration`
- `discount_type`
- `discount_by`
- `apply_on`
- `discount_value`
- `discount_amounts`
- `max_discount_amount`
- `max_redemption`
- `max_redemption_count`
- `redemption_count`
- `max_redemption_count_per_user`
- `max_usage_per_transaction`
- `max_discounted_product_count_per_cart`
- `minimum_order_value`
- `minimum_order_quantity`
- `activation_time` / `activation_time_utc`
- `expiry_time` / `expires_at`
- `eligible_products`
- `buy_products`
- `get_products`
- `eligible_customers`
- `eligible_shipping_zones`

## 4. Coupon Model and Fields

### `offer.Coupon` fields

- `coupon_id` - `CharField(max_length=120)`
- `couponset_id` - `CharField(max_length=120, blank=True)`
- `org_id` - `IntegerField(db_index=True)`
- `coupon_name` - `CharField(max_length=255, blank=True)`
- `coupon_code` - `CharField(max_length=120, db_index=True)`
- `description` - `TextField(blank=True)`
- `is_active` - `BooleanField(default=False)`
- `status` - `CharField(max_length=120, blank=True)`
- `rule_type` - `CharField(max_length=120, blank=True)`
- `coupon_type` - `CharField(max_length=120, blank=True)`
- `show_in_storefront` - `BooleanField(default=False)`
- `restrict_for_guest_user` - `BooleanField(default=False)`
- `restrict_for_offline_payments` - `BooleanField(default=False)`
- `stop_after_this_rule` - `BooleanField(default=False)`
- `apply_once_per_order` - `BooleanField(default=False)`
- `type` - `CharField(max_length=120, blank=True)`
- `duration` - `CharField(max_length=120, blank=True)`
- `discount_type` - `CharField(max_length=120, blank=True)`
- `discount_by` - `CharField(max_length=120, blank=True)`
- `apply_on` - `CharField(max_length=120, blank=True)`
- `discount_value` - `CharField(max_length=120, blank=True)`
- `discount_amounts` - `JSONField(default=list, blank=True)`
- `max_discount_amount` - `CharField(max_length=120, blank=True)`
- `max_redemption` - `IntegerField(default=0)`
- `max_redemption_count` - `IntegerField(default=0)`
- `redemption_count` - `IntegerField(default=0)`
- `max_redemption_count_per_user` - `IntegerField(default=0)`
- `max_usage_per_transaction` - `IntegerField(default=0)`
- `max_discounted_product_count_per_cart` - `CharField(max_length=120, blank=True)`
- `minimum_order_value` - `DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)`
- `minimum_order_quantity` - `CharField(max_length=120, blank=True)`
- `activation_time` - `DateTimeField(null=True, blank=True)`
- `expiry_at` - `CharField(max_length=120, blank=True)`
- `expiry_time` - `DateTimeField(null=True, blank=True)`
- `eligible_products` - `JSONField(default=dict, blank=True)`
- `buy_products` - `JSONField(default=dict, blank=True)`
- `get_products` - `JSONField(default=dict, blank=True)`
- `eligible_customers` - `JSONField(default=dict, blank=True)`
- `eligible_shipping_zones` - `JSONField(default=dict, blank=True)`
- `raw_data` - `JSONField(default=dict, blank=True)`
- `last_synced_at` - `DateTimeField(auto_now=True)`
- `created_at` - `DateTimeField(auto_now_add=True)`

### Org link and new-coupon detection

The org link is `org_id`.

There is no separate `is_synced` flag. Instead, the sync logic uses:

- `coupon_id` + `org_id` as the uniqueness boundary
- `get_or_create(coupon_id=..., org_id=...)` to decide whether a row already exists
- `last_synced_at` to track the most recent successful save
- `created_at` to mark local insert time

The sync code also initializes some fields differently for first insert versus update. For example, `redemption_count` is only pulled from payload on create.

### Timestamp and sync-state fields

The main sync-related fields are:

- `last_synced_at`
- `created_at`
- `activation_time`
- `expiry_time`

There is no explicit `sync_state` column on `Coupon`.

## 5. Polling Mechanism

### Where the polling lives

Polling is implemented as a Django management command:

- `offer/management/commands/poll_zoho_coupons.py`

There is no Celery task in the scanned code for coupon polling.

### How often it runs

The repository task notes document a cron schedule:

- `0 0,6,12,18 * * * python manage.py poll_zoho_coupons`

That means the intended cadence is every 6 hours.

### Per-org or global loop

The command loops over all active stores with a non-empty Zoho org id:

- `Store.objects.filter(is_active=True).exclude(zoho_org_id='')`

So it is effectively per-org via the store list, not a single global org run.

### Duplicate detection and upsert logic

The exact duplicate logic is local-row keyed by `coupon_id` and `org_id`.

High-level behavior:

1. Fetch the live coupon list from Zoho.
2. For each returned coupon row:
   - Resolve the live `coupon_id`.
   - Fetch full detail for that coupon.
   - Upsert the local `Coupon` row with `get_or_create(coupon_id=..., org_id=...)`.
   - If `is_active` is false, delete the local row instead of keeping it.
3. After the loop:
   - Delete local rows whose `coupon_id` is no longer present in the live Zoho list.
   - Delete rows whose `expiry_time` is set and already expired.

### Post-save signal or hook

There is no coupon-specific post-save signal in the scanned code.

There is a separate user-creation signal:

- `shop.signals.welcome_member_notification`

That signal creates a welcome in-app notification for new users, but it is unrelated to coupon insertion or coupon polling.

## 6. Existing Infrastructure

### Celery

Celery does not appear to be set up in the scanned workspace:

- No Celery app, config, or task files were found.
- No Celery package appears in `requirements.txt`.
- The coupon polling path is a management command plus cron-style scheduling in the task notes.

### Redis

Redis is not present in the scanned backend workspace:

- No Redis-related dependency was found in `requirements.txt`.
- No Redis config or cache wiring was found in the inspected files.

### Core package versions

From `requirements.txt`:

- Django 4.2.25
- djangorestframework 3.15.2
- djangorestframework_simplejwt 5.5.0
- requests 2.33.1
- python-dotenv 1.2.2
- django-cors-headers 4.7.0
- dj-database-url 3.0.1
- gunicorn 25.3.0
- whitenoise 6.12.0
- psycopg2-binary 2.9.12
- Pillow 12.2.0

### Push notification / messaging libraries

No push delivery library was found in the backend requirements or code scan:

- No `firebase-admin`
- No FCM client package such as `pyfcm`
- No OneSignal or APNs client package

What does exist is an in-app notification system and email delivery via Django mail.

### Existing notification handling

The backend already has an in-app notification feed:

- `shop.models.UserNotification`
- `shop.services.notifications.create_user_notification()`
- REST endpoints for list, unread count, mark-all-read, and detail update

Notifications are created on events such as:

- user sign-up welcome message
- order placement
- loyalty points earned or deducted
- return submission
- coupon issuance from wallet points

That makes the current system notification-capable at the database/API level, but not push-capable.

## 7. Flutter App Integration

### How the Flutter app communicates with the backend

The Flutter source tree is not present in this workspace, so the actual Flutter implementation cannot be verified here.

What the backend clearly exposes is a REST API under `/api/...`, and the authentication stack is JWT-based:

- `REST_FRAMEWORK` uses `JWTAuthentication`.
- `LoginSerializer` returns `refresh` and `access` tokens.
- There is no session-auth dependency in the API config.

So the backend strongly indicates REST + JWT Bearer usage for the mobile client.

### Firebase / FCM configuration

No Firebase or FCM configuration files were found in this workspace:

- No `pubspec.yaml`
- No `.dart` files
- No `firebase_options.dart`
- No `firebase.json`
- No Flutter source tree at all in the scanned backend repository

Therefore, Firebase/FCM is not verifiable here and should be treated as absent from this workspace unless the mobile repo lives elsewhere.

### Notification handling in Flutter

Not verifiable in this workspace.

The backend has an in-app notification feed, but there is no mobile-side code here to confirm whether the Flutter app displays that feed, registers push tokens, or listens for real-time events.

### Device token storage / backend registration

No backend model, serializer, view, or migration for storing FCM device tokens was found.

That means there is currently no verified server-side device-token registration flow for push delivery.

## Design-Relevant Summary

For a notification system, the current backend provides three important foundations:

1. A stable org/store boundary via `Store` and `org_id`-scoped coupons.
2. A usable in-app notification table and API surface.
3. Event points already emitting notifications during order, reward, and account flows.

What is missing for push notifications is equally clear:

- no FCM/APNs integration
- no device-token model
- no push-dispatch worker
- no verified Flutter token-registration implementation in this workspace

If the goal is to add mobile push, the cleanest extension point is likely the existing `create_user_notification()` event path, with an additional dispatch layer that maps each in-app notification to device tokens.

## Source Files Checked

- `aonegt/settings.py`
- `accounts/models.py`
- `accounts/serializers.py`
- `accounts/services/zoho_inventory_contact.py`
- `accounts/services/zoho_commerce_contact.py`
- `catalog/models.py`
- `offer/models.py`
- `offer/services.py`
- `offer/management/commands/poll_zoho_coupons.py`
- `shop/models.py`
- `shop/services/notifications.py`
- `shop/services/order_sync_state.py`
- `shop/services/zoho_commerce.py`
- `shop/signals.py`
- `shop/urls.py`
- `shop/views.py`
- `zoho_integration/models.py`
- `zoho_integration/services.py`
- `zoho_integration/storefront_collections.py`
- `zoho_integration/admin.py`
- `zoho_integration/migrations/0001_initial.py`
- `shop/migrations/0014_user_notification.py`
- `requirements.txt`
