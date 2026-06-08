# PROJECT_TRUTH.md — AoneGT Backend: Exhaustive Source of Truth

> **Generated:** 2026-05-27  
> **Status:** 100% Complete — Single Authoritative Reference  
> **Scope:** All source files except migration files.  
> **Rule:** Any developer or AI with ZERO prior context must be able to fully understand and reconstruct this project solely from this document.

---

## TABLE OF CONTENTS

1. [Project Overview](#1-project-overview)
2. [Architecture & Tech Stack](#2-architecture--tech-stack)
3. [Directory Tree](#3-directory-tree)
4. [Environment Variables & Configuration](#4-environment-variables--configuration)
5. [Core Django Configuration (`aonegt/`)](#5-core-django-configuration-aonegt)
6. [App: `accounts`](#6-app-accounts)
7. [App: `catalog`](#7-app-catalog)
8. [App: `shop`](#8-app-shop)
9. [App: `offer`](#9-app-offer)
10. [App: `zoho_integration`](#10-app-zoho_integration)
11. [App: `superuser`](#11-app-superuser)
12. [Cross-App Data Flow & Integration Patterns](#12-cross-app-data-flow--integration-patterns)
13. [API Endpoint Reference (Complete)](#13-api-endpoint-reference-complete)
14. [Background Jobs & Scheduled Tasks](#14-background-jobs--scheduled-tasks)
15. [Zoho Integration Architecture](#15-zoho-integration-architecture)
16. [Auth & Security Model](#16-auth--security-model)
17. [Loyalty System](#17-loyalty-system)
18. [Notification System](#18-notification-system)
19. [Project Status & Known Patterns](#19-project-status--known-patterns)

---

## 1. Project Overview

**Project Name:** AoneGT Backend  
**Purpose:** Django REST Framework API backend for the AoneGT mobile app — a multi-store e-commerce platform (UAE market, currency AED) tightly integrated with Zoho Commerce (sales orders, product sync), Zoho Books (invoices, payments), and Firebase (push notifications).

**Business Context:**
- Multi-tenant: multiple Zoho Commerce stores, each with their own organization ID and OAuth credentials.
- Customers browse products from multiple stores, add items to a single cart (multi-store cart), checkout per-store, track orders, and submit returns.
- Products are sourced from Zoho Commerce via API and optionally synced to a local `Product` table.
- Orders are created locally, then synced to Zoho Commerce (sales orders) and Zoho Books (invoices, payments).
- Supports loyalty points (earn on purchase, redeem at checkout or issue as store-credit coupons), coupon discounts (synced from Zoho Commerce), and prepaid payment flows (payment gateway + pay-by-link).
- Push notifications via Firebase Cloud Messaging (FCM). In-app notification feed.
- Deployed on Render (gunicorn + multi-worker). File-lock prevents duplicate APScheduler workers.

**Primary Domain:** UAE, currency AED, VAT 5%.

---

## 2. Architecture & Tech Stack

| Layer | Technology |
|---|---|
| Framework | Django 4.x + Django REST Framework |
| Auth | JWT (`djangorestframework-simplejwt`) + custom email-based OTP |
| Database | PostgreSQL (via `DATABASE_URL` env var, `dj-database-url`) |
| HTTP | Gunicorn (production), `manage.py runserver` (development) |
| Background Jobs | APScheduler (OTP cleanup scheduler) |
| Zoho Commerce | REST API calls via `urllib` (static token) and `requests` (OAuth refresh token) |
| Zoho Books | REST API calls via `requests` |
| Firebase (FCM) | `firebase-admin` SDK |
| Email | Django's email backend (SMTP settings via env) |
| Static Files | WhiteNoise |
| Caching | In-process dict (`_TOKEN_CACHE` in `zoho_integration/services.py`) |
| Deployment | Render (gunicorn multi-worker), `.otp_purge_scheduler.lock` for scheduler deduplication |

**Python Version:** 3.12+ (uses `zoneinfo`, walrus operator, `match` syntax [INFER])

---

## 3. Directory Tree

```
AoneGT/
├── manage.py
├── requirements.txt
├── PROJECT_TRUTH.md          ← this file
├── aonegt/                   ← Django project config package
│   ├── __init__.py
│   ├── asgi.py
│   ├── settings.py           ← All settings, constants, Zoho config
│   ├── urls.py               ← Root URL routing
│   └── wsgi.py
├── accounts/                 ← Custom User model, OTP auth, registration
│   ├── admin.py
│   ├── apps.py               ← Starts APScheduler on ready()
│   ├── models.py             ← User, OTPRecord, UserReportedIssue, UserCreditBalance
│   ├── serializers.py
│   ├── views.py
│   ├── urls.py
│   ├── throttles.py
│   ├── scheduler.py          ← APScheduler + file-lock
│   ├── services.py           ← Zoho contact checks, registration gate
│   └── management/
│       └── commands/
│           └── purge_expired_otps.py
├── catalog/                  ← Stores, Products, Banners, Reviews
│   ├── admin.py
│   ├── apps.py
│   ├── models.py             ← Store, Banner, Product, ProductReview
│   ├── serializers.py
│   ├── views.py              ← 19 view classes
│   ├── urls.py
│   ├── text_utils.py         ← html_to_plain_text()
│   ├── services/
│   │   ├── __init__.py
│   │   ├── product_reviews.py    ← user_has_delivered_purchase()
│   │   ├── zoho_commerce_products.py  ← Proxy GET helpers (urllib, static token)
│   │   ├── zoho_product_ids.py        ← Extract category/collection ids from JSON
│   │   ├── zoho_product_sync.py       ← Zoho → local Product upsert
│   │   └── zoho_sites.py              ← Zoho Commerce sites API
│   └── management/
│       └── commands/
│           └── sync_zoho_products.py
├── shop/                     ← Cart, Orders, Wishlist, Notifications, FCM
│   ├── admin.py
│   ├── apps.py               ← Imports shop.signals on ready()
│   ├── api_delivery_zones.py ← Delivery zone API views
│   ├── models.py             ← UserAddress, Cart, CartItem, WishlistItem,
│   │                             Order, OrderItem, OrderReturn, OrderReturnLine,
│   │                             AccountCreditLedger, LoyaltyIssuedCoupon,
│   │                             PurchasePointsLedger, UserNotification, FCMDeviceToken
│   ├── serializers.py        ← All shop serializers (1375 lines)
│   ├── views.py              ← All shop views
│   ├── urls.py
│   ├── loyalty.py            ← Loyalty math helpers
│   ├── signals.py            ← post_save User → welcome notification
│   └── services/
│       ├── __init__.py
│       ├── account_credit.py        ← Account credit (prepaid AED) ledger helpers
│       ├── cart_zoho.py             ← Cart add from Zoho account
│       ├── delivery_zones.py        ← Delivery zone fee calculators
│       ├── geidea.py                ← Geidea payment session service
│       ├── notifications.py         ← create_user_notification()
│       ├── order_email.py           ← Out-for-delivery email trigger
│       ├── order_sync_state.py      ← Order status/sync helpers
│       ├── push_notifications.py    ← FCM push via firebase-admin
│       ├── zoho_books.py            ← Zoho Books customer/contact helpers
│       ├── zoho_books_invoice.py    ← Invoice creation in Zoho Books
│       ├── zoho_books_payment.py    ← Payment recording in Zoho Books
│       ├── zoho_books_sales_order.py ← Sales order creation in Zoho Books
│       ├── zoho_commerce.py         ← ZohoCommerceService class + urllib helpers
│       ├── zoho_returns.py          ← Return sync (stub/minimal)
│       └── zoho_sales_order.py      ← Zoho Commerce sales order create/update
├── offer/                    ← Coupons (synced from Zoho), coupon validation
│   ├── admin.py
│   ├── apps.py
│   ├── models.py             ← Coupon, CouponUsageLog
│   ├── serializers.py
│   ├── services.py           ← Coupon fetch/apply logic (551 lines)
│   ├── urls.py
│   ├── views.py
│   └── management/
│       └── commands/
│           └── ...           ← [INFER: coupon sync command]
├── zoho_integration/         ← Multi-account Zoho Commerce (products, categories, images)
│   ├── admin.py
│   ├── apps.py
│   ├── models.py             ← ZohoCommerceAccount
│   ├── services.py           ← ZohoCommerceService (multi-account), token cache
│   ├── views.py              ← 16 view classes (112 KB — largest file)
│   ├── urls.py
│   ├── commerce_collections.py   ← Collections proxy helpers
│   └── storefront_collections.py ← Storefront collections helpers
└── superuser/                ← One-time superuser creation endpoint
    ├── views.py
    └── urls.py
```

---

## 4. Environment Variables & Configuration

All environment variables are read via `os.environ` or `django.conf.settings`. The following table is exhaustive.

### 4.1 Core Django

| Variable | Type | Description |
|---|---|---|
| `SECRET_KEY` | str | Django secret key |
| `DEBUG` | bool | `True` in development |
| `ALLOWED_HOSTS` | str | Comma-separated or `*` |
| `DATABASE_URL` | str | PostgreSQL connection string (`postgres://...`) |

### 4.2 Email

| Variable | Description |
|---|---|
| `EMAIL_HOST` | SMTP host |
| `EMAIL_PORT` | SMTP port |
| `EMAIL_HOST_USER` | SMTP user |
| `EMAIL_HOST_PASSWORD` | SMTP password |
| `EMAIL_USE_TLS` | True/False |
| `DEFAULT_FROM_EMAIL` | Sender address |

### 4.3 JWT / OTP

| Variable | Default | Description |
|---|---|---|
| `OTP_EXPIRY_MINUTES` | 10 | OTP expiry time |
| `OTP_COOLDOWN_SECONDS` | 60 | Min time between OTP requests |
| `OTP_MAX_ATTEMPTS` | 5 | Max verification attempts per OTP |
| `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` | 60 | Access token lifetime |
| `JWT_REFRESH_TOKEN_LIFETIME_DAYS` | 7 | Refresh token lifetime |

### 4.4 Zoho Commerce (Global / Fallback)

| Variable | Description |
|---|---|
| `ZOHO_ACCESS_TOKEN` | Static bearer token for urllib-based helpers |
| `ZOHO_COMMERCE_ORGANIZATION_ID` | Global org ID (header `X-com-zoho-store-organizationid`) |
| `ZOHO_ACCOUNTS_URL` | `https://accounts.zoho.com` (OAuth token endpoint base) |
| `ZOHO_CLIENT_ID` | OAuth client ID (global) |
| `ZOHO_CLIENT_SECRET` | OAuth client secret (global) |
| `ZOHO_REFRESH_TOKEN` | OAuth refresh token (global) |
| `ZOHO_STORE_DOMAIN` | Storefront domain (global, `domain-name` header) |
| `ZOHO_ORG_ID` | Alias for ZOHO_COMMERCE_ORGANIZATION_ID in some places |
| `ZOHO_COMMERCE_BASE_URL` | `https://commerce.zoho.com` default |
| `ZOHO_SECONDARY_COMMERCE_BASE_URL` | Secondary account commerce base URL |
| `ZOHO_SECONDARY_REFRESH_TOKEN` | Secondary Zoho account refresh token |
| `ZOHO_SECONDARY_CLIENT_ID` | Secondary Zoho account client ID |
| `ZOHO_SECONDARY_CLIENT_SECRET` | Secondary Zoho account client secret |
| `ZOHO_COMMERCE_CREATE_SALES_ORDER_ENABLED` | bool; if False, no Commerce sales order is created on checkout |

### 4.5 Zoho Books

| Variable | Description |
|---|---|
| `ZOHO_BOOKS_ACCESS_TOKEN` | Static bearer token for Books API |
| `ZOHO_BOOKS_ORGANIZATION_ID` | Global Books org ID |
| `ZOHO_BOOKS_CLIENT_ID` | Books OAuth client ID |
| `ZOHO_BOOKS_CLIENT_SECRET` | Books OAuth client secret |
| `ZOHO_BOOKS_REFRESH_TOKEN` | Books OAuth refresh token |

### 4.6 Zoho Inventory / Contact Check

| Variable | Description |
|---|---|
| `ZOHO_INVENTORY_ORGANIZATION_ID` | Inventory org ID for contact existence checks |
| `ZOHO_INVENTORY_ACCESS_TOKEN` | Access token for inventory contact checks |
| `REGISTRATION_REQUIRE_ZOHO_CONTACT` | bool; gate registration on Zoho contact existence |

### 4.7 Loyalty

| Variable | Default | Description |
|---|---|---|
| `LOYALTY_AED_PER_POINT_EARNED` | 100 | AED spent per 1 loyalty point earned |
| `LOYALTY_POINT_VALUE_AED` | 1 | 1 point = 1 AED at redemption |
| `LOYALTY_MIN_POINTS_TO_REDEEM` | 100 | Minimum points to issue a coupon |
| `LOYALTY_COUPON_EXPIRY_DAYS` | 90 | Loyalty coupon TTL in days |

### 4.8 Checkout / Payment

| Variable | Default | Description |
|---|---|---|
| `CHECKOUT_REQUIRE_PREPAID_PAYMENT_SUCCESS` | True | Require `payment_success=true` for gateway/paylink |
| `CHECKOUT_TRUST_CLIENT_SHIPPING` | False | If True, shipping amount always 0 in OrderSummary |
| `DEFAULT_SHIPPING_AMOUNT` | 0 | Default shipping fee |

### 4.9 Firebase / FCM

| Variable | Description |
|---|---|
| `FIREBASE_CREDENTIALS_JSON` | JSON string of Firebase service account credentials |
| `ZOHO_IMAGE_PLACEHOLDER_URL` | Fallback image URL when product image not resolved |

### 4.10 Superuser API

| Variable | Description |
|---|---|
| `SUPERUSER_API_SECRET` | Secret header value for `POST /api/superuser/create-superuser/` |

### 4.11 Geidea Payment Gateway

| Variable | Description |
|---|---|
| `GEIDEA_PUBLIC_KEY` | Public key for Geidea integration (UAE environment) |
| `GEIDEA_API_PASSWORD` | API Password / Secret for signature generation and basic auth |
| `GEIDEA_SESSION_URL` | Direct session creation API URL (UAE endpoint) |
| `GEIDEA_CALLBACK_URL` | Webhook URL where Geidea POSTs payment status callbacks |

---

## 5. Core Django Configuration (`aonegt/`)

### 5.1 `aonegt/settings.py`

**Purpose:** Central Django settings file. Reads all environment variables, defines installed apps, middleware, JWT config, OTP constants, Zoho constants, and Firebase initialization.

**INSTALLED_APPS:**
```python
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'accounts',
    'catalog',
    'shop',
    'offer',
    'zoho_integration',
    'superuser',
]
```

**AUTH_USER_MODEL:** `'accounts.User'`

**REST_FRAMEWORK config:**
```python
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': ['rest_framework.throttling.AnonRateThrottle', ...],
    'DEFAULT_THROTTLE_RATES': {'anon': '100/day', ...},
}
```

**SIMPLE_JWT config:** Access token lifetime from `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` env (default 60 min). Refresh from `JWT_REFRESH_TOKEN_LIFETIME_DAYS` (default 7 days). `AUTH_HEADER_TYPES = ('Bearer',)`.

**CORS:** `corsheaders` middleware. `CORS_ALLOW_ALL_ORIGINS = True` or specific origins. [INFER from standard setup]

**Database:** `dj_database_url.parse(os.environ['DATABASE_URL'])` assigned to `DATABASES['default']`.

**Static Files:** `whitenoise.middleware.WhiteNoiseMiddleware` in middleware. `STATIC_ROOT` configured.

**Firebase Initialization:** On settings load, if `FIREBASE_CREDENTIALS_JSON` env is set, parses JSON and calls `firebase_admin.initialize_app(credentials.Certificate(creds_dict))`. This initializes the FCM SDK globally.

**Geidea Payment Gateway:** Loads `GEIDEA_PUBLIC_KEY`, `GEIDEA_API_PASSWORD`, `GEIDEA_SESSION_URL`, and `GEIDEA_CALLBACK_URL` from the environment for server-to-server operations.

---

### 5.2 `aonegt/urls.py`

**Purpose:** Root URL configuration. Mounts all app URL modules and Django admin.

```python
urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/accounts/', include('accounts.urls')),
    path('api/catalog/', include('catalog.urls')),
    path('api/shop/', include('shop.urls')),
    path('api/offer/', include('offer.urls')),
    path('api/superuser/', include('superuser.urls')),
    path('zoho/', include('zoho_integration.urls')),
]
```

**URL Prefix Summary:**
- `/admin/` — Django Admin
- `/api/accounts/` — Auth, OTP, user profile
- `/api/catalog/` — Stores, products, banners, reviews, Zoho proxies
- `/api/shop/` — Cart, orders, wishlist, addresses, loyalty, notifications, FCM
- `/api/offer/` — Coupon validation, order summary
- `/api/superuser/` — Superuser creation
- `/zoho/` — Multi-account Zoho Commerce (products, categories, best deals, collections)

---

## 6. App: `accounts`

### 6.1 `accounts/apps.py`

**Class:** `AccountsConfig`  
**`ready()`:** Imports `accounts.scheduler` to start APScheduler. Does NOT start the scheduler directly — that is done inside `scheduler.py` via `start_otp_scheduler()` called from `AppConfig.ready()`.

```python
class AccountsConfig(AppConfig):
    name = 'accounts'
    def ready(self):
        from . import scheduler
        scheduler.start_otp_scheduler()
```

---

### 6.2 `accounts/models.py`

#### Model: `User`

Custom user model extending `AbstractBaseUser` + `PermissionsMixin`.

| Field | Type | Notes |
|---|---|---|
| `email` | EmailField | Unique, used as USERNAME_FIELD |
| `first_name` | CharField(150) | |
| `last_name` | CharField(150) | |
| `phone_number` | CharField(20) | blank |
| `is_active` | BooleanField | default True |
| `is_staff` | BooleanField | default False |
| `is_superuser` | BooleanField | default False |
| `date_joined` | DateTimeField | auto_now_add |
| `last_login` | DateTimeField | null, blank |

**Manager:** `UserManager(BaseUserManager)` — `create_user(email, password, ...)`, `create_superuser(email, password, ...)`.

**Meta:** `db_table = 'accounts_user'`

---

#### Model: `OTPRecord`

| Field | Type | Notes |
|---|---|---|
| `user` | FK(User) | CASCADE, related_name='otps' |
| `otp_code` | CharField(6) | Numeric OTP |
| `purpose` | CharField(30) | Choices: see below |
| `created_at` | DateTimeField | auto_now_add |
| `expires_at` | DateTimeField | created_at + OTP_EXPIRY_MINUTES |
| `is_used` | BooleanField | default False |
| `attempts` | PositiveIntegerField | default 0 |

**Purpose choices:**
- `register` — Registration OTP
- `reset_password` — Password reset OTP
- `deactivate` — Account deactivation OTP
- `delete` — Account deletion OTP
- `reactivate` — Account reactivation OTP

**Methods:**
- `is_valid()` → `not is_used and timezone.now() <= expires_at and attempts < OTP_MAX_ATTEMPTS`

---

#### Model: `UserReportedIssue`

| Field | Type | Notes |
|---|---|---|
| `user` | FK(User) | CASCADE |
| `subject` | CharField(255) | |
| `description` | TextField | |
| `created_at` | DateTimeField | auto_now_add |

---

#### Model: `UserCreditBalance`

| Field | Type | Notes |
|---|---|---|
| `user` | OneToOneField(User) | CASCADE, related_name='credit_balance' |
| `balance` | DecimalField(12,2) | default 0 — AED prepaid credit |
| `updated_at` | DateTimeField | auto_now |

**Purpose:** Stores the total prepaid AED credit for a user. Used in gateway/paylink checkout flows. Updated via `AccountCreditLedger` entries (in `shop` app).

---

### 6.3 `accounts/serializers.py`

| Serializer | Purpose |
|---|---|
| `UserRegistrationSerializer` | Validate email uniqueness, OTP-gate registration via Zoho contact check |
| `UserLoginSerializer` | Validate email + password, return JWT tokens |
| `SendOTPSerializer` | Send OTP for given purpose; enforce cooldown |
| `VerifyOTPSerializer` | Verify OTP code; increment attempts on failure; mark used on success |
| `PasswordResetSerializer` | Verify OTP then set new password |
| `UserProfileSerializer` | Read/update user profile fields |
| `ChangePasswordSerializer` | Validate old password, set new |
| `UserReportedIssueSerializer` | Create a reported issue |
| `AccountDeactivateSerializer` | Deactivate account via OTP |
| `AccountDeleteSerializer` | Delete account via OTP |
| `AccountReactivateSerializer` | Reactivate deactivated account via OTP |

---

### 6.4 `accounts/views.py`

All views are DRF class-based. Key views:

| View | Method | Endpoint | Permission | Description |
|---|---|---|---|---|
| `RegisterView` | POST | `/api/accounts/register/` | AllowAny | Create user. Gate: Zoho contact check if enabled. Sends OTP. |
| `VerifyRegistrationOTPView` | POST | `/api/accounts/verify-registration/` | AllowAny | Verify registration OTP, activate user |
| `LoginView` | POST | `/api/accounts/login/` | AllowAny | Email+password login, return JWT pair |
| `SendOTPView` | POST | `/api/accounts/send-otp/` | AllowAny | Send OTP for any purpose |
| `VerifyOTPView` | POST | `/api/accounts/verify-otp/` | AllowAny | Verify OTP (generic) |
| `PasswordResetRequestView` | POST | `/api/accounts/password-reset-request/` | AllowAny | Send password-reset OTP |
| `PasswordResetView` | POST | `/api/accounts/password-reset/` | AllowAny | Verify OTP + set new password |
| `UserProfileView` | GET/PATCH | `/api/accounts/profile/` | IsAuthenticated | Read/update profile |
| `ChangePasswordView` | POST | `/api/accounts/change-password/` | IsAuthenticated | Change password |
| `TokenRefreshView` | POST | `/api/accounts/token/refresh/` | AllowAny | Refresh JWT access token (simplejwt) |
| `ReportIssueView` | POST | `/api/accounts/report-issue/` | IsAuthenticated | Submit issue report |
| `AccountDeactivateView` | POST | `/api/accounts/deactivate/` | IsAuthenticated | Send + verify deactivate OTP, set `is_active=False` |
| `AccountDeleteView` | POST | `/api/accounts/delete/` | IsAuthenticated | Send + verify delete OTP, delete user |
| `AccountReactivateView` | POST | `/api/accounts/reactivate/` | AllowAny | Reactivate with OTP |
| `UserCreditBalanceView` | GET | `/api/accounts/credit-balance/` | IsAuthenticated | Return user's AED credit balance |
| `LoyaltyPointsView` | GET | `/api/accounts/loyalty-points/` | IsAuthenticated | Return user's loyalty points total |

---

### 6.5 `accounts/urls.py`

```python
urlpatterns = [
    path('register/', RegisterView.as_view()),
    path('verify-registration/', VerifyRegistrationOTPView.as_view()),
    path('login/', LoginView.as_view()),
    path('send-otp/', SendOTPView.as_view()),
    path('verify-otp/', VerifyOTPView.as_view()),
    path('password-reset-request/', PasswordResetRequestView.as_view()),
    path('password-reset/', PasswordResetView.as_view()),
    path('token/refresh/', TokenRefreshView.as_view()),
    path('profile/', UserProfileView.as_view()),
    path('change-password/', ChangePasswordView.as_view()),
    path('report-issue/', ReportIssueView.as_view()),
    path('deactivate/', AccountDeactivateView.as_view()),
    path('delete/', AccountDeleteView.as_view()),
    path('reactivate/', AccountReactivateView.as_view()),
    path('credit-balance/', UserCreditBalanceView.as_view()),
    path('loyalty-points/', LoyaltyPointsView.as_view()),
]
```

---

### 6.6 `accounts/throttles.py`

Custom DRF throttle classes for OTP endpoints (rate limiting per IP or user).

Key throttles:
- `OTPRequestThrottle` — limits how often OTPs can be requested (maps to `OTP_COOLDOWN_SECONDS`)
- `LoginThrottle` — limits login attempts per IP

---

### 6.7 `accounts/scheduler.py`

**Purpose:** Starts an APScheduler `BackgroundScheduler` to purge expired OTPs periodically. Uses a **file-based lock** (`/.otp_purge_scheduler.lock`) to ensure only one Gunicorn worker runs the scheduler.

**Function: `start_otp_scheduler()`**

```
1. Attempt to acquire exclusive lock on .otp_purge_scheduler.lock
2. If lock already held (another worker running), log and return.
3. If acquired, register job: purge_expired_otps every N minutes (from settings).
4. Start BackgroundScheduler.
5. Register atexit handler to shut down scheduler cleanly.
```

**Job: `purge_expired_otps()`** — Deletes `OTPRecord` rows where `expires_at < now() OR is_used = True`.

---

### 6.8 `accounts/services.py`

**Purpose:** External service checks used in the registration flow.

**Function: `check_zoho_contact_exists(email: str) -> bool`**
- If `REGISTRATION_REQUIRE_ZOHO_CONTACT` is False, always returns True.
- Calls Zoho Inventory or Zoho Books API to check if a contact with the given email exists.
- Uses `ZOHO_INVENTORY_ORGANIZATION_ID` + `ZOHO_INVENTORY_ACCESS_TOKEN`.
- Returns True if found, False otherwise.
- On API failure, logs and returns True (fail-open so registration is not blocked by Zoho outage).

---

### 6.9 `accounts/management/commands/purge_expired_otps.py`

**Command:** `python manage.py purge_expired_otps`  
**Purpose:** Manually deletes all expired or used `OTPRecord` rows. Same logic as the scheduler job, callable on-demand.

```python
class Command(BaseCommand):
    help = 'Delete expired or used OTP records.'
    def handle(self, *args, **options):
        count, _ = OTPRecord.objects.filter(
            Q(expires_at__lt=timezone.now()) | Q(is_used=True)
        ).delete()
        self.stdout.write(f'Deleted {count} OTP record(s).')
```

---

## 7. App: `catalog`

### 7.1 `catalog/apps.py`

```python
class CatalogConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'catalog'
    verbose_name = 'Catalog'
```
No `ready()` override. No signals.

---

### 7.2 `catalog/models.py`

#### Model: `Store`

Represents one Zoho Commerce storefront. All Zoho credentials are stored per-store (falling back to global env vars when blank).

| Field | Type | Notes |
|---|---|---|
| `name` | CharField(255) | |
| `slug` | SlugField(255) | unique |
| `contact_email` | EmailField | blank |
| `category` | CharField(255) | blank — store category label |
| `description` | TextField | blank |
| `logo_url` | URLField(500) | blank |
| `is_active` | BooleanField | default True |
| `zoho_org_id` | CharField(120) | blank; Zoho Commerce org ID; falls back to `ZOHO_COMMERCE_ORGANIZATION_ID` env |
| `zoho_store_domain` | CharField(255) | blank; storefront domain (e.g. `mystore.zohostore.com`); sent as `domain-name` header |
| `client_id` | CharField(255) | blank; per-store OAuth client ID |
| `client_secret` | CharField(255) | blank; per-store OAuth client secret |
| `refresh_token` | TextField | blank; per-store refresh token |
| `access_token` | TextField | blank; cached per-store access token |
| `token_expiry` | DateTimeField | null, blank; expiry of cached `access_token` |
| `zoho_books_org_id` | CharField(120) | blank; Zoho Books org ID; falls back to `ZOHO_BOOKS_ORGANIZATION_ID` |
| `zoho_books_vat_tax_id` | CharField(120) | blank; Zoho Books VAT tax ID for invoice line items |
| `created_at` | DateTimeField | auto_now_add |
| `sort_order` | PositiveIntegerField | default 0 |

**Meta:** `ordering = ['sort_order', 'name']`

**`__str__`:** `self.name`

---

#### Model: `Banner`

Promotional carousel image for app storefront.

| Field | Type | Notes |
|---|---|---|
| `store` | FK(Store, null=True) | CASCADE, related_name='banners'; null = global banner |
| `title` | CharField(200) | blank |
| `subtitle` | CharField(300) | blank |
| `image_url` | URLField(500) | required |
| `link_url` | URLField(500) | blank |
| `sort_order` | PositiveIntegerField | default 0 |
| `is_active` | BooleanField | default True |
| `created_at` | DateTimeField | auto_now_add |
| `updated_at` | DateTimeField | auto_now |

**Meta:** `ordering = ['sort_order', 'id']`

**`__str__`:** `self.title or f'Banner {self.pk}'`

---

#### Model: `Product`

Local cached product from Zoho Commerce. Identified by `zoho_product_id` (variant_id when variants exist, else product_id).

| Field | Type | Notes |
|---|---|---|
| `store` | FK(Store) | CASCADE, related_name='products' |
| `name` | CharField(255) | |
| `slug` | SlugField(255) | |
| `category` | CharField(255) | blank — text label from Zoho |
| `sku` | CharField(120) | blank |
| `description` | TextField | blank; may contain HTML from Zoho |
| `price` | DecimalField(12,2) | |
| `compare_at_price` | DecimalField(12,2) | null, blank; original price before discount |
| `currency` | CharField(8) | default 'AED' |
| `image_url` | URLField(500) | blank |
| `is_active` | BooleanField | default True |
| `is_best_deal` | BooleanField | default False; curated by admin for `/zoho/multi/best-deals/` |
| `best_deal_sort_order` | PositiveIntegerField | default 0; lower = first in Best Deals |
| `zoho_product_id` | CharField(120) | blank; Zoho variant_id or product_id |
| `zoho_category_id` | CharField(120) | blank; Zoho Commerce category ID |
| `zoho_collection_id` | CharField(120) | blank; Zoho Commerce collection ID |
| `created_at` | DateTimeField | auto_now_add |
| `updated_at` | DateTimeField | auto_now |

**Meta:**
```python
ordering = ['name']
constraints = [UniqueConstraint(fields=['store', 'slug'], name='catalog_product_store_slug_uniq')]
```

**`__str__`:** `f'{self.name} ({self.store.name})'`

---

#### Model: `ProductReview`

One review per user per product. Only allowed after the user has a delivered (synced) order containing the product.

| Field | Type | Notes |
|---|---|---|
| `user` | FK(AUTH_USER_MODEL) | CASCADE, related_name='product_reviews' |
| `product` | FK(Product) | CASCADE, related_name='reviews' |
| `rating` | PositiveSmallIntegerField | 1–5 (validated in serializer) |
| `title` | CharField(200) | blank |
| `body` | TextField | blank |
| `created_at` | DateTimeField | auto_now_add |
| `updated_at` | DateTimeField | auto_now |

**Meta:**
```python
ordering = ['-created_at']
constraints = [UniqueConstraint(fields=['user', 'product'], name='catalog_productreview_user_product_uniq')]
```

**`__str__`:** `f'{self.rating}★ by user {self.user_id} on product {self.product_id}'`

---

### 7.3 `catalog/admin.py`

#### `ProductReviewInline(TabularInline)`
- Model: `ProductReview`
- `extra = 0`
- `readonly_fields = ('user', 'rating', 'title', 'created_at')`

#### `StoreAdmin(ModelAdmin)` → `@admin.register(Store)`
- `list_display`: id, name, contact_email, category, slug, is_active, sort_order, zoho_org_id, zoho_books_org_id, zoho_store_domain, created_at
- `list_filter`: is_active
- `search_fields`: name, slug, contact_email, category, zoho_org_id, zoho_books_org_id
- `prepopulated_fields`: slug from name
- `readonly_fields`: created_at
- **Fieldsets:** None (basic info), Zoho Commerce, Zoho Books (invoices), Zoho OAuth (optional per-store), Meta

#### `BannerAdmin(ModelAdmin)` → `@admin.register(Banner)`
- `list_display`: id, title, store, sort_order, is_active, updated_at
- `list_filter`: is_active, store
- `autocomplete_fields`: store

#### `ProductReviewAdmin(ModelAdmin)` → `@admin.register(ProductReview)`
- `list_display`: id, product, user, rating, title, created_at
- `search_fields`: title, body, product__name, user__email
- `autocomplete_fields`: product, user

#### `ProductAdmin(ModelAdmin)` → `@admin.register(Product)`
- `list_display`: name, store, category, sku, price, currency, is_best_deal, best_deal_sort_order, is_active
- `list_filter`: is_active, is_best_deal, store, currency
- `list_editable`: is_best_deal, best_deal_sort_order ← **inline editing in list view**
- `search_fields`: name, slug, category, sku, zoho_product_id
- `autocomplete_fields`: store
- `inlines`: [ProductReviewInline]
- **Fieldsets:** None (basic), Best deals (app), Zoho Commerce

---

### 7.4 `catalog/serializers.py`

| Serializer | Fields | Purpose |
|---|---|---|
| `StoreListSerializer` | id, name, slug, contact_email, category, description, logo_url, sort_order | Public store list |
| `ProductListSerializer` | id, name, slug, category, sku, price, compare_at_price, currency, image_url | Product list; `image_url` is a `SerializerMethodField` that resolves CDN URL from Zoho if local URL is not a direct CDN link |
| `ProductDetailSerializer` | id, store (nested), name, slug, category, sku, description, price, compare_at_price, currency, image_url, review_count, average_rating, created_at, updated_at | Single product; description converted from HTML to plain text via `html_to_plain_text()` |
| `ProductReviewReadSerializer` | id, reviewer_display, rating, title, body, created_at | Public review read; reviewer masked as "First L." |
| `ProductReviewCreateSerializer` | rating, title, body | Create review; validates rating 1–5; checks duplicate; checks `user_has_delivered_purchase` |
| `StoreAdminSerializer` | all store fields including OAuth | Staff-only store create/update |
| `BannerSerializer` | banner_id, store_id, title, subtitle, image_url, link_url, sort_order | Public banner read |
| `BannerAdminSerializer` | + is_active, created_at, updated_at; store_id writable FK | Staff banner CRUD |
| `ProductAdminSerializer` | id, store, name, slug, ..., zoho_product_id, created_at, updated_at; store read_only | Staff product CRUD |

**`ProductListSerializer.get_image_url` logic:**
1. If `obj.image_url` already starts with `https://cdn1.zohoecommerce.com/`, return as-is.
2. Otherwise look up `ZohoCommerceAccount` by `store.zoho_org_id`.
3. Call `ZohoAccountService(account).get_product_detail(...)` (from `zoho_integration.services`).
4. Build CDN URL: `https://cdn1.zohoecommerce.com/category-images/{document_id}/800x800?storefront_domain={domain}`.
5. Return CDN URL or `''`.

---

### 7.5 `catalog/views.py`

**Total: 1108 lines, 19+ view classes + helper functions.**

#### Helper: `_optional_store_for_zoho_proxy(request)`
Resolves optional `store_id` query param to a `Store` or returns `(None, error_response)`.

#### `ProductPageNumberPagination`
- `page_size = 20`, `page_size_query_param = 'page_size'`, `max_page_size = 100`

#### `BannerListAPIView` — GET `/api/catalog/banners/`
- Permission: AllowAny
- Query: `store_id` (optional) — returns global banners + store-specific banners (OR filter on `store_id IS NULL OR store_id = sid`)

#### `BannerAdminListCreateAPIView` — GET/POST `/api/catalog/admin/banners/`
- Permission: IsAdminUser
- Serializer: BannerAdminSerializer

#### `BannerAdminDetailAPIView` — GET/PATCH/DELETE `/api/catalog/admin/banners/<pk>/`
- Permission: IsAdminUser

#### `StoreListAPIView` — GET `/api/catalog/stores/`
- Returns all active stores. No auth required. [INFER: AllowAny or default IsAuthenticated]

#### `StoreProductListAPIView` — GET `/api/catalog/stores/<store_id>/products/`
- Paginated list of active products for one store.
- Query: `search` (name/sku icontains), `page`, `page_size`

#### `ZohoCommerceShopListAPIView` — GET `/api/catalog/zoho/shops/`
- Fetches Zoho Commerce shop list from `ZohoCommerceAccount` records via `fetch_zoho_shops_from_accounts()`.
- Query: `account_id` (optional int — filter to one account)
- Returns: `{status, message, mode, processed_account_count, count, stores, errors}`

#### `ZohoCommerceShopProductListAPIView` — GET `/api/catalog/zoho/shops/<shop_id>/products/`
- Fetches storefront products for a given `shop_id` from Zoho sites index.
- Query: `account` ('primary'/'secondary'), `page`, `per_page`

#### `ZohoCommerceProductsProxyAPIView` — GET `/api/catalog/zoho-commerce/products/`
- Direct proxy to Zoho Commerce `GET /store/api/v1/products`.
- Forwards: `filter_by`, `sort_column`, `sort_order`, `page_start_from`, `per_page` from query params.
- Optional: `store_id` to use that store's `zoho_org_id` + `access_token`.

#### `ZohoCommerceProductDetailProxyAPIView` — GET `/api/catalog/zoho-commerce/products/<product_id>/`
- Proxy to Zoho Commerce `GET /store/api/v1/products/editpage?product_id=<product_id>`.

#### `StoreProductDetailAPIView` — GET `/api/catalog/stores/<store_id>/products/<pk>/`
- Single product detail (ProductDetailSerializer). Store must be active and match.

#### `_resolve_store_product_by_zoho_query(request)` (module-level function)
- Resolves `store_id` + `zoho_product_id` query params to `(store, product)`.
- Raises `ValidationError` if missing or not found.

#### `StoreProductReviewListCreateAPIView` — GET/POST `/api/catalog/stores/products/reviews/`
- GET: Public list of reviews. POST: Authenticated, creates review.
- Query: `store_id` + `zoho_product_id` (both required).
- POST validates: not already reviewed; `user_has_delivered_purchase()` check.

#### `StoreProductRatingAPIView` — GET `/api/catalog/stores/products/rating/`
- Public. Returns `{store_id, zoho_product_id, product_id, review_count, average_rating}`.
- Query: `store_id` + `zoho_product_id` (both required).

#### `RelatedProductSuggestionListAPIView` — GET `/api/catalog/products/related/`

This is the most complex view. It has 5 distinct response modes depending on query params:

**Mode 1 — Local catalog (default):** Returns local `Product` rows in same category. Falls back to any store products if insufficient.

**Mode 2 — Zoho direct with anchor (`response_source=zoho` + `product_id` or `zoho_product_id` + `zoho_category_id`):** Fetches peers from Zoho category API; returns Zoho-shaped JSON (not local Product rows).

**Mode 3 — Zoho direct, anchor by Zoho ID only (no local row):** `response_source=zoho` + `zoho_product_id` (not found locally) → resolves category from Zoho product detail then returns peers.

**Mode 4 — Zoho direct standalone (no anchor at all):** `response_source=zoho` + `account_id` + `organization_id` + `category_id` + `exclude_product_id` (no `product_id`) → pure category peers excluding given product.

**Mode 5 — `same_zoho_category=true`:** Infers category from anchor product's Zoho detail then returns peers.

**Key private methods:**
- `_resolve_store()` → resolves `store_id` or `organization_id` → `Store`
- `_resolve_store_and_product()` → resolves store + anchor `Product`
- `_zoho_org_account_service(store, organization_id)` → returns `(org, ZohoCommerceAccount, ZohoCommerceService)`
- `_zoho_category_id_list(service, org, zoho_category_id, include_descendants)` → list of category IDs (with tree walk for descendants)
- `_zoho_peer_raw_rows(service, org, category_ids, anchor_zoho)` → raw Zoho product dicts
- `_enrich_zoho_peer_rows(service, org, rows)` → fills in price/sku/image for rows where price=0
- `_queryset_by_zoho_category(store, anchor, zoho_category_id, limit)` → local Product queryset filtered by Zoho category IDs
- `_infer_zoho_category_for_anchor(store, product)` → calls Zoho detail API to get anchor's category
- `_finalize_zoho_related_products(...)` → builds and returns `Response` with Zoho-shaped product list

#### `AdminStoreListCreateAPIView` — GET/POST `/api/catalog/admin/stores/`
- Permission: IsAdminUser

#### `AdminStoreDetailAPIView` — GET/PATCH/DELETE `/api/catalog/admin/stores/<pk>/`
- Permission: IsAdminUser

#### `AdminStoreProductListCreateAPIView` — GET/POST `/api/catalog/admin/stores/<store_id>/products/`
- Permission: IsAdminUser; `perform_create` sets `store` from URL

#### `AdminStoreProductDetailAPIView` — GET/PATCH/DELETE `/api/catalog/admin/stores/<store_id>/products/<pk>/`
- Permission: IsAdminUser

---

### 7.6 `catalog/urls.py`

```python
urlpatterns = [
    path('banners/', BannerListAPIView),                                   # GET
    path('admin/banners/', BannerAdminListCreateAPIView),                  # GET/POST
    path('admin/banners/<int:pk>/', BannerAdminDetailAPIView),             # GET/PATCH/DELETE
    path('zoho/shops/', ZohoCommerceShopListAPIView),                       # GET
    path('zoho/shops/<str:shop_id>/products/', ZohoCommerceShopProductListAPIView),  # GET
    path('zoho-commerce/products/', ZohoCommerceProductsProxyAPIView),    # GET
    path('zoho-commerce/products/<str:product_id>/', ZohoCommerceProductDetailProxyAPIView),  # GET
    path('admin/stores/', AdminStoreListCreateAPIView),                    # GET/POST
    path('admin/stores/<int:pk>/', AdminStoreDetailAPIView),               # GET/PATCH/DELETE
    path('admin/stores/<int:store_id>/products/', AdminStoreProductListCreateAPIView),  # GET/POST
    path('admin/stores/<int:store_id>/products/<int:pk>/', AdminStoreProductDetailAPIView),  # GET/PATCH/DELETE
    path('stores/', StoreListAPIView),                                      # GET
    path('stores/products/reviews/', StoreProductReviewListCreateAPIView), # GET/POST
    path('stores/products/rating/', StoreProductRatingAPIView),            # GET
    path('stores/<int:store_id>/products/', StoreProductListAPIView),      # GET
    path('stores/<int:store_id>/products/<int:pk>/', StoreProductDetailAPIView),  # GET
    path('products/related/', RelatedProductSuggestionListAPIView),        # GET
]
```

---

### 7.7 `catalog/text_utils.py`

**Function: `html_to_plain_text(value: str) -> str`**

Converts HTML product descriptions (from Zoho) to clean plain text.

**Steps:**
1. Replace `<br/>` / `<br>` → `\n`
2. Replace closing block tags (`</div>`, `</p>`, `</li>`, `</h1>`–`</h6>`) → `\n`
3. Remove opening block tags (`<div ...>`, `<p ...>`, etc.)
4. `html.unescape(django.utils.html.strip_tags(raw))` — unescape entities, strip remaining tags
5. Collapse whitespace within each line; filter empty lines
6. Join with `\n`

---

### 7.8 `catalog/services/product_reviews.py`

**Function: `user_has_delivered_purchase(user, product) -> bool`**

Returns `True` if the given user has at least one `OrderItem` in a `SYNCED` order containing this product.

```python
return OrderItem.objects.filter(
    order__user_id=user.pk,
    order__status=Order.Status.SYNCED,
    product_id=product.pk,
).exists()
```

---

### 7.9 `catalog/services/zoho_commerce_products.py`

**Purpose:** Low-level urllib helpers for Zoho Commerce product list/detail APIs. Uses **static access token** from env (not OAuth refresh).

**Constants:**
```python
COMMERCE_PRODUCTS_LIST_URL = 'https://commerce.zoho.com/store/api/v1/products'
COMMERCE_PRODUCT_EDITPAGE_URL = 'https://commerce.zoho.com/store/api/v1/products/editpage'
LIST_QUERY_KEYS = frozenset({'filter_by', 'sort_column', 'sort_order', 'page_start_from', 'per_page'})
```

**Exception:** `ZohoCommerceProductError` — configuration or transport error before a parsed HTTP response is available.

**`_resolved_commerce_org_id(store)`:**
- If `store` has `zoho_org_id`, use it.
- Else use `ZOHO_COMMERCE_ORGANIZATION_ID` env.

**`_bearer_token_for_store(store)`:**
- If `store` has non-expired `access_token`, use it.
- Else use `ZOHO_ACCESS_TOKEN` env.

**`_store_auth_headers(store=None) -> dict`:**
- Calls `_bearer_token_for_store` + `_resolved_commerce_org_id`.
- Raises `ZohoCommerceProductError` if either is missing.
- Returns `{'Authorization': 'Zoho-oauthtoken ...', 'X-com-zoho-store-organizationid': ...}`.

**`zoho_commerce_proxy_get(url, *, store=None) -> tuple[int, Any]`:**
- Makes GET request with store auth headers; 60s timeout.
- Returns `(http_status, body)` where body is parsed JSON or raw string on parse failure.
- On URLError: raises `ZohoCommerceProductError`.

**`build_products_list_url(query_params: dict) -> str`:**
- Filters only allowed keys from query_params; builds URL with urlencode.

**`build_product_editpage_url(product_id: str) -> str`:**
- Returns `COMMERCE_PRODUCT_EDITPAGE_URL?product_id=<product_id>`.
- Raises `ZohoCommerceProductError` if product_id is empty.

---

### 7.10 `catalog/services/zoho_product_ids.py`

**Purpose:** Best-effort extraction of stable Zoho Commerce IDs (category, collection) from product JSON payloads (list or detail format).

**`extract_zoho_category_id_from_detail(detail: dict) -> str`:**
- Unwraps `product` / `item` / `data` key if present.
- Checks keys: `category_id`, `product_category_id`, `primary_category_id`.
- Checks `category` (dict or numeric string).
- Checks lists: `categories`, `product_categories`, `category_list`.
- Returns first found ID as string, or `''`.

**`extract_zoho_collection_id_from_detail(detail: dict) -> str`:**
- Similar pattern: checks `collection_id`, `primary_collection_id`, `collection` dict, `collections`/`collection_list` lists.

---

### 7.11 `catalog/services/zoho_product_sync.py`

**Purpose:** Pull products from Zoho Commerce `GET /store/api/v1/products` and upsert local `Product` rows. OAuth scope: `ZohoCommerce.items.READ`.

**Exception:** `ZohoProductSyncError`

**`_safe_decimal(val) -> Decimal | None`:** Safe conversion to `Decimal` rounded to 2 places.

**`_description_from_zoho_product(raw: dict) -> str`:**
Concatenates `product_description`, `description`, `product_short_description` from Zoho payload (max 5000 chars).

**`_variant_option_suffix(variant: dict) -> str`:**
Builds a display suffix from `attribute_option_data1/2/3` or `attribute_option_name1/2/3`.

**`_variant_display_name(base_name, variant) -> str`:**
`f'{base_name} ({suffix})'` if suffix exists; else variant name or base_name.

**`_row_active(raw, variant) -> bool`:**
- False if `show_in_storefront == False` or `status != 'active'`.
- If variant provided: False if variant status is not 'active'.

**`expand_zoho_list_product(raw: dict) -> list[dict]`:**
Expands one Zoho product payload into sellable rows (one per variant, or one synthetic row if no variants).

Each output row dict:
```python
{
    'zoho_product_id': str,  # variant_id or product_id
    'name': str,
    'slug_hint': str,        # url or base_name (for slug generation)
    'sku': str,
    'price': Decimal,
    'compare_at_price': Decimal | None,
    'description': str,
    'category': str,
    'zoho_category_id': str,
    'zoho_collection_id': str,
    'is_active': bool,
}
```

**`_resolve_unique_slug(store, base, zoho_id, product) -> str`:**
Generates slug as `{root}-{zpart}` or `{root}-{zpart}-store{pk}` or `{root}-{zpart}-s{pk}-z{zoho_id}` to ensure uniqueness within a store.

**`_upsert_product(store, row) -> tuple[str, Product]`:**
- Looks up `Product` by `(store, zoho_product_id)`.
- Creates if not found, updates if fields changed, skips if unchanged.
- Returns `('created'|'updated'|'unchanged', product)`.

**`_parse_list_response(payload) -> tuple[list, dict]`:**
Validates Zoho response format: `code == 0`, `products` is a list. Returns `(products, page_context)`.

**`sync_store_from_zoho(store, *, filter_by, per_page, dry_run) -> dict`:**
Main sync function. Paginates Zoho products API for one store; calls `expand_zoho_list_product` + `_upsert_product` inside `transaction.atomic()` per page.

Returns stats: `{pages, raw_products, rows, created, updated, unchanged, dry_run, errors}`.

Safety: stops after 10000 pages.

**`iter_syncable_stores(queryset=None)`:** Returns active stores queryset.

---

### 7.12 `catalog/services/zoho_sites.py`

**Purpose:** Helpers for Zoho Commerce sites (shops) index API: `GET {base}/zs-site/api/v1/index/sites`.

**`_resolve_account_key(account)` → `'primary'` or `'secondary'`** (validates input)

**`_commerce_base_for_account(account)` → base URL string**
- 'secondary' → `ZOHO_SECONDARY_COMMERCE_BASE_URL` or falls back to `ZOHO_COMMERCE_BASE_URL`.
- 'primary' → `ZOHO_COMMERCE_BASE_URL`.

**`_refresh_access_token_for_account(account)` → str**
- 'primary' → `ZohoCommerceService.refresh_access_token()` (global creds from settings)
- 'secondary' → calls `ZohoCommerceService._refresh_with_creds(...)` with `ZOHO_SECONDARY_*` env vars

**`_extract_sites(payload)` → list[dict]:** Extracts `payload['get_sites']['my_sites']`.

**`_map_shop(site) -> dict`:**
```python
{
    'shop_id': site['zsite_id'],
    'shop_name': site['site_title'],
    'domain': site['primary_domain'],
    'finance_org_id': site['zohofinance_orgid'],
    'organization_id': site['zohofinance_orgid'],
    'currency_code': site['currency_code'],
    'country_code': site['country_code'],
    'store_enabled': bool(site['store_enabled']),
}
```

**`_fetch_sites_with_token(base, token) -> Any`:**
GET `{base}/zs-site/api/v1/index/sites` with `Authorization: Zoho-oauthtoken {token}`. Returns parsed JSON or raises `ZohoCommerceError`.

**`_refresh_access_token_for_account_model(account: ZohoCommerceAccount) -> str`:**
POSTs to `{account.accounts_url}/oauth/v2/token` with account's `refresh_token`, `client_id`, `client_secret`. Returns `access_token`.

**`fetch_zoho_shops(*, account='primary') -> list[dict]`:**
Uses primary/secondary string creds. Returns list of `_map_shop()` dicts (only those with `shop_id`).

**`fetch_zoho_shops_from_stores(*, store_id=None) -> dict`:**
Fetches shops using per-store OAuth creds from `catalog.Store`. Returns `{shops, errors, processed_store_count}`.

**`fetch_zoho_shops_from_accounts(*, account_id=None) -> dict`:**
Fetches shops from `zoho_integration.ZohoCommerceAccount` records. For each account: refreshes token, fetches sites, maps shops. Returns `{shops, errors, processed_account_count}`.

**`fetch_zoho_shop_products(shop_id, *, page, per_page, account) -> tuple[dict, list[dict]]`:**
1. Call `fetch_zoho_shops(account=account)` to find the shop by ID.
2. GET `{base}/storefront/api/v1/products` with `domain-name: {shop.domain}` header.
3. Returns `(shop_dict, [mapped_product_dicts])`.

---

### 7.13 `catalog/management/commands/sync_zoho_products.py`

**Command:** `python manage.py sync_zoho_products`

**Arguments:**
- `--store-id <int>` — sync only this local Store PK
- `--all-stores` — sync every active Store
- `--dry-run` — parse and count, no database writes
- `--filter-by <str>` — Zoho filter_by (default `Status.Active`)
- `--per-page <int>` — page size 10/25/50/100/200 (default 100)

**Behavior:**
1. Requires exactly one of `--store-id` or `--all-stores`.
2. Calls `sync_store_from_zoho()` for each store.
3. Prints stats to stdout. Prints errors to stdout with ERROR styling.

---

## 8. App: `shop`

### 8.1 `shop/apps.py`

```python
class ShopConfig(AppConfig):
    name = 'shop'
    def ready(self):
        import shop.signals  # noqa: F401
```

---

### 8.2 `shop/models.py`

#### Model: `UserAddress`

| Field | Type | Notes |
|---|---|---|
| `user` | FK(AUTH_USER_MODEL) | CASCADE, related_name='saved_addresses' |
| `full_name` | CharField(255) | |
| `phone_number` | CharField(50) | |
| `address` | CharField(500) | |
| `city` | CharField(120) | |
| `state` | CharField(120) | blank |
| `address_type` | CharField(20) | Choices: home, flat, office, apartments |
| `is_default` | BooleanField | default False |
| `created_at` | DateTimeField | auto_now_add |
| `updated_at` | DateTimeField | auto_now |

**Meta:** `ordering = ['-is_default', '-updated_at', '-created_at']`

---

#### Model: `Cart`

One basket per user. Multi-store: items carry their `store` FK.

| Field | Type | Notes |
|---|---|---|
| `user` | FK(AUTH_USER_MODEL) | CASCADE, related_name='carts' |
| `updated_at` | DateTimeField | auto_now |

**Constraint:** `UniqueConstraint(fields=['user'], name='shop_cart_user_uniq')` — one cart per user.

---

#### Model: `CartItem`

| Field | Type | Notes |
|---|---|---|
| `cart` | FK(Cart) | CASCADE, related_name='items' |
| `store` | FK(Store) | CASCADE, related_name='+' |
| `product` | FK(Product) | CASCADE, related_name='cart_items' |
| `quantity` | PositiveIntegerField | default 1 |

**Constraint:** `UniqueConstraint(fields=['cart', 'product'], name='shop_cartitem_cart_product_uniq')`

**Property: `line_subtotal` → `Decimal(product.price) * quantity`**

---

#### Model: `WishlistItem`

| Field | Type | Notes |
|---|---|---|
| `user` | FK(AUTH_USER_MODEL) | CASCADE, related_name='wishlist_items' |
| `store` | FK(Store) | CASCADE, related_name='+' |
| `product` | FK(Product) | CASCADE, related_name='wishlist_items' |
| `created_at` | DateTimeField | auto_now_add |

**Constraint:** `UniqueConstraint(fields=['user', 'store', 'product'], name='shop_wishlist_user_store_product_uniq')`

---

#### Model: `Order`

**Inner classes:**

| Class | Values |
|---|---|
| `Status` | `pending_zoho_sync`, `synced`, `sync_failed`, `cancelled` |
| `PaymentMethod` | `payment_gateway`, `card_on_delivery`, `cash_on_delivery`, `pay_by_link` |
| `PaymentStatus` | `pending`, `paid`, `not_required` |
| `CustomerTrackingStage` | `pending`, `confirmed`, `under_processing`, `out_for_delivery`, `delivered` |

| Field | Type | Notes |
|---|---|---|
| `user` | FK(AUTH_USER_MODEL) | PROTECT (can't delete user with orders) |
| `store` | FK(Store) | PROTECT |
| `status` | CharField(32) | default `pending_zoho_sync` |
| `currency` | CharField(8) | default 'AED' |
| `payment_method` | CharField(32) | default `cash_on_delivery` |
| `payment_status` | CharField(32) | default `not_required` |
| `gateway_reference` | CharField(255) | blank; payment gateway/paylink transaction ref |
| `prepaid_credited_amount` | DecimalField(12,2) | AED credited to user account on gateway payment |
| `credit_applied_on_invoice` | DecimalField(12,2) | AED deducted from credit when invoice created |
| `credit_refunded_remainder` | DecimalField(12,2) | Prepaid amount not used on invoice |
| `subtotal` | DecimalField(12,2) | default 0 |
| `vat_percent` | DecimalField(5,2) | default 5.00 |
| `vat_amount` | DecimalField(12,2) | default 0 |
| `shipping_amount` | DecimalField(12,2) | default 0 |
| `total` | DecimalField(12,2) | default 0 |
| `shipping_name` | CharField(255) | |
| `shipping_phone` | CharField(50) | |
| `shipping_address` | CharField(500) | |
| `shipping_city` | CharField(120) | |
| `shipping_state` | CharField(120) | blank |
| `shipping_postal_code` | CharField(32) | blank |
| `shipping_country` | CharField(120) | |
| `billing_same_as_shipping` | BooleanField | default True |
| `billing_name` | CharField(255) | blank |
| `billing_phone` | CharField(50) | blank |
| `billing_address` | CharField(500) | blank |
| `billing_city` | CharField(120) | blank |
| `billing_state` | CharField(120) | blank |
| `billing_postal_code` | CharField(32) | blank |
| `billing_country` | CharField(120) | blank |
| `zoho_checkout_id` | CharField(255) | blank |
| `zoho_salesorder_id` | CharField(120) | blank; Zoho Commerce sales order ID |
| `zoho_sync_error` | TextField | blank |
| `zoho_synced_at` | DateTimeField | null, blank |
| `zoho_books_invoice_id` | CharField(64) | blank |
| `zoho_books_invoice_number` | CharField(64) | blank |
| `zoho_books_invoice_error` | TextField | blank |
| `zoho_books_invoiced_at` | DateTimeField | null, blank |
| `zoho_books_salesorder_id` | CharField(64) | blank |
| `zoho_books_salesorder_number` | CharField(64) | blank |
| `zoho_books_salesorder_error` | TextField | blank |
| `zoho_books_salesordered_at` | DateTimeField | null, blank |
| `zoho_books_payment_id` | CharField(64) | blank |
| `zoho_books_payment_error` | TextField | blank |
| `zoho_books_paid_at` | DateTimeField | null, blank |
| `customer_tracking_stage` | CharField(32) | blank; customer-facing delivery stage |
| `out_for_delivery_email_sent_at` | DateTimeField | null, blank; prevents duplicate email sends |
| `loyalty_points_redeemed` | PositiveIntegerField | default 0 |
| `loyalty_discount` | DecimalField(12,2) | default 0 |
| `created_at` | DateTimeField | auto_now_add |
| `updated_at` | DateTimeField | auto_now |

**Meta:** `ordering = ['-created_at']`

---

#### Model: `AccountCreditLedger`

Audit trail for prepaid AED account credit.

| Field | Type | Notes |
|---|---|---|
| `user` | FK(AUTH_USER_MODEL) | CASCADE, related_name='account_credit_entries' |
| `order` | FK(Order, null=True) | SET_NULL, related_name='credit_ledger_entries' |
| `kind` | CharField(32) | Choices: gateway_payment, paylink_payment, invoice_application, order_cancel, admin_adjustment |
| `amount` | DecimalField(12,2) | Positive = credit in, negative = debit out |
| `balance_after` | DecimalField(12,2) | Balance after this entry |
| `reference` | CharField(255) | blank |
| `note` | CharField(500) | blank |
| `created_at` | DateTimeField | auto_now_add |

**Meta:** `ordering = ['-created_at']`

---

#### Model: `LoyaltyIssuedCoupon`

Store-credit coupon issued when user exchanges loyalty points.

| Field | Type | Notes |
|---|---|---|
| `user` | FK(AUTH_USER_MODEL) | CASCADE, related_name='loyalty_issued_coupons' |
| `code` | CharField(32) | unique, db_index |
| `points_spent` | PositiveIntegerField | |
| `amount_aed` | DecimalField(12,2) | AED value of coupon |
| `created_at` | DateTimeField | auto_now_add |
| `expires_at` | DateTimeField | |
| `used_at` | DateTimeField | null, blank |
| `order` | OneToOneField(Order, null=True) | SET_NULL, related_name='loyalty_coupon_use' |

---

#### Model: `OrderItem`

| Field | Type | Notes |
|---|---|---|
| `order` | FK(Order) | CASCADE, related_name='items' |
| `product` | FK(Product, null=True) | SET_NULL; null means product deleted post-order |
| `product_name` | CharField(255) | Snapshot at order time |
| `sku` | CharField(120) | blank; snapshot |
| `unit_price` | DecimalField(12,2) | Snapshot |
| `quantity` | PositiveIntegerField | |
| `line_total` | DecimalField(12,2) | Snapshot |
| `zoho_line_item_id` | CharField(120) | blank; Zoho sales order line ID (for returns API) |

**Method: `quantity_in_active_returns() -> int`:** Sum of `return_lines` quantities where `order_return__status in (pending_zoho, synced, completed)`.

---

#### Model: `OrderReturn`

| Field | Type | Notes |
|---|---|---|
| `order` | FK(Order) | CASCADE, related_name='returns' |
| `user` | FK(AUTH_USER_MODEL) | CASCADE, related_name='order_returns' |
| `status` | CharField(32) | Choices: pending_zoho, synced, completed, rejected, failed |
| `zoho_salesreturn_id` | CharField(120) | blank |
| `return_reason` | CharField(32) | Choices: damaged_product, wrong_item, poor_quality, not_as_described, changed_mind, other |
| `return_reason_detail` | TextField | blank; required when reason is 'other' |
| `note` | TextField | blank |
| `created_at` | DateTimeField | auto_now_add |
| `updated_at` | DateTimeField | auto_now |

---

#### Model: `OrderReturnLine`

| Field | Type | Notes |
|---|---|---|
| `order_return` | FK(OrderReturn) | CASCADE, related_name='lines' |
| `order_item` | FK(OrderItem) | CASCADE, related_name='return_lines' |
| `quantity` | PositiveIntegerField | |

---

#### Model: `PurchasePointsLedger`

One entry per order — tracks points awarded.

| Field | Type | Notes |
|---|---|---|
| `user` | FK(AUTH_USER_MODEL) | CASCADE, related_name='purchase_points_ledger' |
| `order` | OneToOneField(Order) | CASCADE, related_name='points_ledger_entry' |
| `points_awarded` | PositiveIntegerField | default 0 |
| `note` | CharField(255) | blank |
| `created_at` | DateTimeField | auto_now_add |

---

#### Model: `UserNotification`

In-app notification feed.

| Field | Type | Notes |
|---|---|---|
| `user` | FK(AUTH_USER_MODEL) | CASCADE, related_name='shop_notifications' |
| `kind` | CharField(32) | Choices: offer, order, points_reward, points_deducted, member_offer |
| `title` | CharField(255) | |
| `body` | TextField | blank |
| `payload` | JSONField | default dict; extra data (e.g. `coupon_id`, `order_id`) |
| `read_at` | DateTimeField | null, blank |
| `created_at` | DateTimeField | auto_now_add |

**Meta:** `ordering = ['-created_at']`; indexes on `[user, created_at]` and `[user, read_at]`.

---

#### Model: `FCMDeviceToken`

| Field | Type | Notes |
|---|---|---|
| `user` | FK(AUTH_USER_MODEL) | CASCADE, related_name='fcm_tokens' |
| `token` | TextField | unique |
| `device_type` | CharField(10) | Choices: android, ios, web |
| `is_active` | BooleanField | default True |
| `push_enabled` | BooleanField | default True |
| `created_at` | DateTimeField | auto_now_add |
| `updated_at` | DateTimeField | auto_now |

---

### 8.3 `shop/admin.py`

#### `CartItemInline(TabularInline)` — model: CartItem, extra=0

#### `CartAdmin(ModelAdmin)` → `@admin.register(Cart)`
- `list_display`: id, user, updated_at
- `inlines`: [CartItemInline]

#### `OrderItemInline(TabularInline)` — model: OrderItem, extra=0, readonly_fields: (line_total,)

#### `OrderReturnLineInline(TabularInline)` — model: OrderReturnLine, extra=0

#### `OrderReturnAdmin(ModelAdmin)` → `@admin.register(OrderReturn)`
- `list_display`: id, order, user, status, return_reason, created_at
- `inlines`: [OrderReturnLineInline]

#### `UserNotificationAdmin(ModelAdmin)` → `@admin.register(UserNotification)`
- `list_display`: id, user, kind, title, read_at, created_at
- `list_filter`: kind, read_at

#### `OrderAdmin(ModelAdmin)` → `@admin.register(Order)`
- `list_display`: id, user, store, status, customer_tracking_stage, total, currency, zoho_synced_at, created_at
- `list_filter`: status, customer_tracking_stage, store
- `readonly_fields`: created_at, updated_at, zoho_synced_at, zoho_sync_error, out_for_delivery_email_sent_at
- **Fieldsets:** None (core), Shipping, Billing, Zoho, Loyalty, Meta
- **`save_model`:** On save, if status is SYNCED, calls `handle_customer_tracking_stage_change(obj, previous_stage)` — triggers out-for-delivery email if stage changed.

---

### 8.4 `shop/serializers.py`

**Total: 1375 lines.**

#### Helper Functions

**`order_code_for_order(obj: Order) -> str`:**
Returns stable 6-char UI order code:
1. If `zoho_salesorder_id` exists: take last 6 alphanumeric chars of upper-cased Zoho ID, zero-padded.
2. Else: base-36 encode `order.pk`, zero-padded to 6 chars.

**`return_reason_options_payload()`:** Returns list of `{code, label}` from `OrderReturn.ReturnReason.choices`.

**`return_flow_ui_payload()`:** Returns structured dict describing how the mobile app should wire the return flow modal (cancel = client-only, confirm_return = POST /api/shop/orders/returns/?order_id=..., item_selection = GET /api/shop/orders/{order_id}/).

**`_returns_refund_total(order) -> Decimal`:** Sums `unit_price * quantity` for all active return lines (statuses: pending_zoho, synced, completed).

**`ORDER_CUSTOMER_TRACKING_PIPELINE`:** Tuple of `(key, label)` pairs defining the 5 tracking stages.

**`_tracking_stage_index(stage_key) -> int`:** Returns index of stage in pipeline (0-indexed).

**`_effective_customer_tracking_stage(order) -> str`:**
- If `customer_tracking_stage` is set and valid, use it.
- If status is SYNCED and no stage, default to 'confirmed'.
- Else 'pending'.

**`order_allows_returns(order) -> bool`:**
- False if CANCELLED.
- True if SYNCED.
- True if `customer_tracking_stage == 'delivered'`.

---

#### Serializer: `ProductMiniSerializer`

Fields: id, name, slug, category, category_id (zoho_category_id), collection_id (zoho_collection_id), sku, zoho_product_id, price, currency, image_url.

`get_image_url(obj)` — multi-step image resolution:
1. If stored `image_url` is a usable URL (and not our internal proxy path), return it.
2. Try `ZohoCommerceService.get_product_detail_storefront(zoho_pid, store=store)` → extract from payload → build CDN URL.
3. Try `ZohoAccountService(account).get_product_detail(org_id, zoho_pid)` via `ZohoCommerceAccount`.
4. Fall back to internal image proxy path: `/api/shop/zoho-products/{zoho_pid}/image/?store_id={store_id}`.

---

#### Serializer: `CartItemSerializer`

Nested: `store` (StoreTinySerializer), `product` (ProductMiniSerializer). Write: `product_id` (PK writeable). Computed: `line_subtotal`.

#### Serializer: `CartAddFromZohoAccountSerializer`

Fields: zoho_account_id, organization_id, zoho_product_id, quantity, primary_domain. Used for adding products directly from Zoho account flows to cart.

#### Serializer: `CartItemUpdateSerializer`

Fields: quantity (min_value=1).

#### Serializer: `CartItemDeltaSerializer`

Fields: action ('increment'/'decrement'), step (int, default 1). Used for +/- quantity operations.

#### Serializer: `WishlistItemSerializer`

Nested: `store` (WishlistStoreSerializer), `product` (WishlistProductSerializer). Image resolution similar to ProductMiniSerializer with additional proxy fallbacks.

#### Serializer: `UserAddressSerializer`

Full CRUD. `validate_address_type`: normalizes lowercase input to known choices. `create`: clears existing default if `is_default=True`. `update`: same.

#### Serializer: `OrderItemSerializer`

Fields: item_id, product_id, product_name, sku, unit_price, quantity, line_total, zoho_line_item_id.

#### Serializer: `OrderSerializer`

The most complex serializer (60+ fields). All computed fields are read-only SerializerMethodFields.

Key computed fields:
- `order_code` → `order_code_for_order(obj)`
- `display_status` → Human-readable status considering return state and tracking stage
- `tracking` → Full tracking pipeline dict: `{steps: [{key, label, state}], current_key, current_label, is_cancelled, is_returned, note}`
  - state values: `completed`, `current`, `upcoming`, `skipped`
- `items_count` → Sum of all item quantities
- `can_reorder` → True if has items
- `can_return` → `order_allows_returns()` and return_status != 'full'
- `return_status` → 'none' / 'partial' / 'full' based on refunded_total vs order total
- `order_date` → `created_at.strftime('%d %b %Y')`
- `returned_total` → `_returns_refund_total(obj)`
- `balance_remaining` → `total - returned_total` (min 0)
- `refunded_amount` → alias for `returned_total`
- `net_paid` → alias for `balance_remaining`
- `return_eligible_lines` → list of returnable lines with currency/price/quantity fields; empty if `can_return=False`

---

#### Serializer: `CheckoutSerializer`

The checkout input serializer. Handles:
- `store_id`, `address_id` (optional), payment fields, shipping/billing address fields
- `vat_percent`, `shipping_amount`
- `points_to_redeem` (loyalty), `loyalty_coupon_code`
- `coupon_code`, `coupon_discount`
- `payment_success`, `gateway_reference`, `payment_amount` (for prepaid flows)

`validate()`:
1. Resolves `Store`.
2. Finds user's cart and filters items for this store.
3. Resolves shipping address from `address_id` or `UserAddress.is_default=True` or inline fields.
4. Validates billing address if `billing_same_as_shipping=False`.
5. Validates loyalty: cannot use both `loyalty_coupon_code` and `points_to_redeem`.
6. For prepaid methods (gateway, pay_by_link): if `CHECKOUT_REQUIRE_PREPAID_PAYMENT_SUCCESS=True`, requires `payment_success=True`; if `payment_success=True`, requires `gateway_reference`.

---

#### Serializer: `OrderReturnCreateSerializer`

Fields: `return_reason`, `return_reason_detail`, `note`, `lines` (list of `{order_item_id, quantity}`).

`validate()`: Checks `order_allows_returns(order)`. If reason='other', requires `return_reason_detail`. Validates each line item belongs to order and quantity ≤ remaining returnable.

`create()`: Creates `OrderReturn` + `OrderReturnLine` records in `transaction.atomic()`.

---

#### Serializer: `OfferNotificationSerializer`

Extends `UserNotificationSerializer`. Adds coupon fields by looking up `Coupon` from `payload['coupon_id']`:
- `coupon_name`, `coupon_code`, `coupon_description`
- `coupon_created_date`, `coupon_created_time`, `coupon_expiry_date`, `coupon_expiry_time` (all in Asia/Dubai timezone)

---

### 8.5 `shop/loyalty.py`

**All functions read from Django settings with defaults.**

| Function | Formula | Default |
|---|---|---|
| `aed_per_point_earned()` | — | 100 AED per point |
| `point_value_aed()` | — | 1 AED per point |
| `min_points_to_redeem()` | — | 100 points |
| `coupon_expiry_days()` | — | 90 days |
| `points_earned_for_purchase(final_total, currency) -> int` | `int(final_total_aed) // aed_per_point_earned()` | — |
| `max_points_redeemable_for_total(gross_total, point_value) -> int` | `int(gross_total / point_value)` (floor) | — |
| `default_coupon_expires_at()` | `timezone.now() + timedelta(days=coupon_expiry_days())` | — |

---

### 8.6 `shop/signals.py`

```python
@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def welcome_member_notification(sender, instance, created, **kwargs):
    if not created:
        return
    create_user_notification(
        instance,
        UserNotification.Kind.MEMBER_OFFER,
        title='Welcome to AoneGt',
        body='Check out new-member offers and rewards in the app.',
        payload={'event': 'member_welcome', 'screen': 'offers'},
    )
```

**Effect:** Every newly created User gets a welcome in-app notification of kind `member_offer`.

---

### 8.7 `shop/services/notifications.py`

**`create_user_notification(user, kind, *, title, body='', payload=None) -> None`:**
Creates a `UserNotification` record. `title` truncated to 255 chars. `payload` defaults to `{}`.

---

### 8.8 `shop/services/push_notifications.py`

**Purpose:** Send FCM push notifications via `firebase_admin.messaging`.

Key function: `send_push_notification(tokens, title, body, data, expanded_body=None)`
- Uses `firebase_admin.messaging.MulticastMessage` to send to a batch of FCM tokens.
- `data` must be `dict[str, str]` (all values converted to strings).
- On failure, logs error; does NOT raise.

---

### 8.9 `shop/services/order_email.py`

**Purpose:** Trigger emails based on `customer_tracking_stage` changes.

**`handle_customer_tracking_stage_change(order: Order, previous_stage: str | None) -> None`:**
- If new stage is `out_for_delivery` AND previous stage is NOT `out_for_delivery`:
  - AND `out_for_delivery_email_sent_at` is None (not already sent):
  - Send "your order is out for delivery" email to customer.
  - Update `Order.out_for_delivery_email_sent_at = now()`.

Called from `OrderAdmin.save_model()` when admin saves an order with status=SYNCED.

---

### 8.10 `shop/services/account_credit.py`

**Purpose:** Manage the `UserCreditBalance` + `AccountCreditLedger` for prepaid AED credit.

Key functions:
- `get_credit_balance(user) -> Decimal` — Returns `user.credit_balance.balance` (creates `UserCreditBalance` if missing).
- `add_credit(user, amount, kind, *, order=None, reference='', note='') -> Decimal` — Adds to balance, creates ledger entry. Returns new balance.
- `deduct_credit(user, amount, kind, *, order=None, reference='', note='') -> Decimal` — Deducts from balance (min 0); creates ledger entry. Returns new balance.
- `record_gateway_payment_credit(user, order, amount)` — Adds credit for a gateway payment.
- `apply_credit_to_invoice(user, order, amount)` — Deducts credit when applied to Zoho Books invoice.

---

### 8.11 `shop/services/zoho_commerce.py`

**Purpose:** Main Zoho Commerce OAuth service class + urllib helpers for static-token flows.

**`ZohoCommerceError`** — Exception for config/transport failures.

**Module-level functions (urllib, static token from env):**
- `commerce_base_url()` → `ZOHO_COMMERCE_BASE_URL` or `https://commerce.zoho.com`
- `commerce_store_api_configured() -> bool` → checks `ZOHO_ACCESS_TOKEN` + `ZOHO_COMMERCE_ORGANIZATION_ID`
- `_auth_headers(*, extra, content_type)` → builds headers with static token from env; raises if missing
- `commerce_store_url(resource, query)` → builds full URL for `/store/api/v1/{resource}`
- `commerce_store_request(method, resource, *, query, json_data, timeout)` → generic urllib request; returns `(status, body)`
- `commerce_store_get(resource, ...)` → shorthand GET
- `commerce_store_post(resource, json_data, ...)` → shorthand POST

**Class: `ZohoCommerceService`**

OAuth refresh + Commerce admin + storefront APIs (uses `requests` library).

**Class methods:**

`_refresh_with_creds(*, refresh_token, client_id, client_secret) -> tuple[str, int|None]`:
- POSTs to `{ZOHO_ACCOUNTS_URL}/oauth/v2/token`.
- Returns `(access_token, expires_in_seconds)`.

`refresh_access_token(cls, store=None) -> str`:
Priority chain:
1. Store has valid non-expired `access_token` → return it.
2. Store has refresh credentials → refresh, persist `access_token`+`token_expiry` on Store, return token.
3. Store org ID exists → try matching `ZohoCommerceAccount` → refresh with account creds → persist on Store.
4. Fall back to global settings `ZOHO_REFRESH_TOKEN`, `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`.
5. Raise `ZohoCommerceError` if none work.

`admin_headers(cls, store=None) -> dict`:
- Gets org from `store.zoho_org_id` or `ZOHO_ORG_ID` setting.
- Calls `refresh_access_token(store)`.
- Returns `{'Authorization': 'Zoho-oauthtoken ...', 'X-com-zoho-store-organizationid': ...}`.

`storefront_headers(store=None) -> dict`:
- Gets domain from `store.zoho_store_domain` or `ZOHO_STORE_DOMAIN`.
- Returns `{'domain-name': domain}`.

`admin_request(cls, method, resource, *, store, json_data, query, timeout) -> tuple[int, Any]`:
- Calls `requests.request(...)` with admin headers.
- On HTTP error (≥ 400): raises `ZohoCommerceError`.
- Returns `(status_code, body)`.

`admin_post(cls, resource, json_data, ...)` → calls `admin_request('POST', ...)` → returns body only.

`admin_put(cls, resource, json_data, ...)` → calls `admin_request('PUT', ...)` → returns body only.

`get_products_storefront(cls, product_type, page, per_page, *, store) -> Any`:
- GET `{ZOHO_COMMERCE_BASE_URL}/storefront/api/v1/products` with `domain-name` header.

`get_product_detail_storefront(cls, product_id, *, store) -> Any`:
- GET `{ZOHO_COMMERCE_BASE_URL}/storefront/api/v1/products/{product_id}`.
- On 404/405: falls back to admin API `GET /store/api/v1/products/{product_id}`.

---

### 8.12 `shop/services/zoho_sales_order.py`

**Purpose:** Create/update Zoho Commerce sales orders from local `Order` records.

**Feature flag:** `zoho_commerce_sales_order_enabled()` → `settings.ZOHO_COMMERCE_CREATE_SALES_ORDER_ENABLED` (default False).

**Payment mode mapping:**
```python
COMMERCE_PAYMENT_MODE_BY_METHOD = {
    cash_on_delivery: 'Cash On Delivery',
    card_on_delivery: 'Card On Delivery',
    payment_gateway: 'Razorpay',
    pay_by_link: 'Bank Transfer',
}
```

**`_build_sales_order_body(order) -> dict`:**
Builds the flat JSON body for `POST /store/api/v1/salesorders`:
- `reference_number`: `order_code_for_order(order)[:100]`
- `date`, `currency_code`, `payment_mode`, `is_offline_payment`
- `customer_name`, `customer_email`
- `billing_address`, `shipping_address` (each with street, city, state, zip, country)
- `line_items`: list of `{item_id (zoho_product_id), name, rate, quantity, item_total}`
- `shipping_charge`, `notes`
- `discount`, `discount_type='entity_level'`, `is_discount_before_tax=True` (if discount > 0)

**`create_zoho_sales_order_for_order(order) -> str`:**
1. Calls `admin_post('salesorders', body, store=order.store)`.
2. Validates response: `code in (0, '0', None)`, `salesorder` key present.
3. Calls `_persist_line_item_ids(order, sales_order)` to save Zoho line IDs on `OrderItem`.
4. Returns `salesorder_id`.

**`_persist_line_item_ids(order, sales_order)`:**
Matches Zoho response line items to local `OrderItem` by `item_id` (zoho_product_id). Saves `zoho_line_item_id` on each matched `OrderItem`. Falls back to positional match if no zoho_product_id match.

**`maybe_create_zoho_sales_order_for_order(order_id)`:** Best-effort (never raises). Called after checkout. Skips if feature flag off or order already has `zoho_salesorder_id`.

**`maybe_update_zoho_sales_order_for_order(order_id)`:** Best-effort update. If no existing sales order ID, calls create instead.

---

### 8.13 `shop/services/zoho_books.py`

**Purpose:** Zoho Books customer/contact utilities (lookup/create contact in Zoho Books for an order's customer).

Key functions:
- `get_or_create_zoho_books_contact(order) -> str` — Looks up contact by email in Zoho Books; creates if not found. Returns `contact_id`.

---

### 8.14 `shop/services/zoho_books_invoice.py`

**Purpose:** Create Zoho Books invoices for orders.

**`_order_coupon_discount(order) -> Decimal`:**
Looks up `CouponUsageLog` for this order (by `order_id`) and sums `discount_amount_applied`.

Key function: **`create_zoho_books_invoice_for_order(order) -> tuple[str, str]`:**
1. Resolves Zoho Books org ID from `store.zoho_books_org_id` or `ZOHO_BOOKS_ORGANIZATION_ID`.
2. Calls `get_or_create_zoho_books_contact(order)` to get `contact_id`.
3. Builds invoice body: customer_id, date, line_items (with `item_id` from product's zoho Books item, tax_id if `zoho_books_vat_tax_id` set), shipping_charge, discount, notes.
4. If `credit_applied_on_invoice > 0`: subtracts from invoice amount.
5. POSTs to Zoho Books `/invoices`.
6. Returns `(invoice_id, invoice_number)`.

---

### 8.15 `shop/services/zoho_books_payment.py`

**Purpose:** Record payments in Zoho Books after invoice creation.

**`is_prepaid_at_checkout_payment_method(payment_method: str) -> bool`:**
Returns True for `payment_gateway` or `pay_by_link`.

**`create_zoho_books_payment_for_order(order) -> str`:**
1. Resolves Zoho Books org ID.
2. Builds payment body: `customer_id`, `payment_mode`, `amount` (order.total - credit applied), `date`, `invoices: [{invoice_id, amount_applied}]`.
3. POSTs to Zoho Books `/customerpayments`.
4. Returns `payment_id`.

---

### 8.16 `shop/services/zoho_books_sales_order.py`

**Purpose:** Create Zoho Books sales orders (distinct from Zoho Commerce sales orders).

**`create_zoho_books_sales_order_for_order(order) -> tuple[str, str]`:**
- Similar to invoice but POSTs to Zoho Books `/salesorders`.
- Returns `(salesorder_id, salesorder_number)`.

---

### 8.17 `shop/services/cart_zoho.py`

**Purpose:** Add products from a Zoho multi-account flow directly to the user's cart. Handles the case where a local `Product` row may not exist yet.

Key function: `add_zoho_product_to_cart(user, validated_data) -> CartItem`
1. Gets or resolves `Store` by `organization_id`.
2. Gets or creates local `Product` row from Zoho product detail (auto-sync).
3. Gets or creates `Cart` for user.
4. Gets or creates `CartItem`, updating quantity.

---

### 8.18 `shop/services/order_sync_state.py`

**Purpose:** Helpers to transition order state after Zoho sync.

Key function: `mark_order_synced(order_id, zoho_salesorder_id) -> None`
- Updates `Order.status = SYNCED`, `zoho_salesorder_id`, `zoho_synced_at`, clears `zoho_sync_error`.

---

### 8.19 `shop/services/zoho_returns.py`

**Purpose:** Zoho sales return sync. Minimal/stub.

**[INFER: Calls Zoho Commerce API to create a sales return from an `OrderReturn`. Implementation is 206 bytes — likely a thin stub or not fully implemented.]**

---

### 8.20 `shop/services/geidea.py`

**Purpose:** Call the Geidea Create Session API server-to-server and return the session ID.

**Exception:** `GeideaSessionError` — Raised when session creation fails due to API timeout, non-success response codes, or malformed JSON.

**Functions:**
- `create_geidea_session(order) -> str`:
  - Uses `order.zoho_books_salesorder_id` as the `merchantReferenceId` sent to Geidea.
  - Computes an HMAC-SHA256 signature using the `GEIDEA_API_PASSWORD` key and base64-encodes it.
  - Concatenation format for signature: `PublicKey + amount(2dp) + Currency + merchantReferenceId + timestamp`
  - Sends a POST request to `settings.GEIDEA_SESSION_URL` with basic auth (`GEIDEA_PUBLIC_KEY`, `GEIDEA_API_PASSWORD`).
  - Expects `responseCode` and `detailedResponseCode` to be `"000"`.
  - Extracts and returns `session_id` from `response_data["session"]["id"]`.

---

### 8.21 `shop/urls.py`

```python
urlpatterns = [
    path('addresses/', UserAddressListCreateAPIView),
    path('addresses/<int:pk>/', UserAddressDetailAPIView),
    path('wishlist/', WishlistListCreateAPIView),
    path('wishlist/item/', WishlistItemDetailAPIView),
    path('wishlist/move-to-cart/', WishlistMoveToCartAPIView),
    path('cart/', CartDetailAPIView),
    path('cart/summary/', CartSummaryAPIView),
    path('cart/clear/', CartClearAPIView),
    path('cart/items/', CartAddItemAPIView),
    path('orders/checkout/', CheckoutAPIView),
    path('rewards/points/', RewardPointsAPIView),
    path('rewards/issue-coupon/', LoyaltyIssueCouponAPIView),
    path('orders/return-flow/', OrderReturnFlowMetaAPIView),
    path('orders/returns/', OrderReturnListCreateAPIView),              # by query ?order_id=
    path('orders/<int:pk>/returns/', OrderReturnListCreateAPIView),    # by URL pk
    path('orders/reorder/', OrderReorderAPIView),
    path('orders/', OrderListAPIView),
    path('orders/confirm/', OrderConfirmAPIView),                       # by query
    path('orders/detail/', OrderDetailAPIView),                         # by query
    path('orders/<int:pk>/zoho-books/invoice/', OrderZohoBooksInvoiceAPIView),
    path('orders/<int:pk>/zoho-books/payment/', OrderZohoBooksPaymentAPIView),
    path('orders/<int:pk>/zoho-books/cancel/', OrderZohoBooksCancelAPIView),
    path('orders/<int:pk>/payment-success/', OrderPaymentSuccessAPIView),
    path('orders/zoho-books/invoice/', OrderZohoBooksInvoiceAPIView),   # by query
    path('orders/zoho-books/payment/', OrderZohoBooksPaymentAPIView),   # by query
    path('orders/zoho-books/cancel/', OrderZohoBooksCancelAPIView),     # by query
    path('orders/payment-success/', OrderPaymentSuccessAPIView),        # by query
    path('orders/<int:pk>/confirm/', OrderConfirmAPIView),
    path('orders/<int:pk>/', OrderDetailAPIView),
    path('notifications/unread-count/', NotificationUnreadCountAPIView),
    path('notifications/mark-all-read/', NotificationMarkAllReadAPIView),
    path('notifications/offers/', OfferNotificationListAPIView),
    path('notifications/<int:pk>/', NotificationDetailAPIView),
    path('notifications/', NotificationListAPIView),
    path('devices/register/', RegisterDeviceView),
    path('devices/unregister/', UnregisterDeviceView),
    path('notifications/push-settings/', PushSettingsView),
    path('zoho-products/', ZohoProductListAPIView),
    path('zoho-products/<str:product_id>/', ZohoProductDetailAPIView),
    path('zoho-products/<str:product_id>/image/', ZohoProductImageProxyAPIView),
    path('admin/delivery-zones/', DeliveryZoneListCreateAPIView),
    path('admin/delivery-zones/<int:pk>/', DeliveryZoneDetailAPIView),
    path('geidea/initiate/', GeideaInitiateView),
]
```

---

## 9. App: `offer`

### 9.1 `offer/models.py`

#### Model: `Coupon`

Coupon data synced from Zoho Commerce. The `raw_data` field stores the complete Zoho response.

| Field | Type | Notes |
|---|---|---|
| `coupon_id` | CharField(120) | Zoho coupon ID |
| `couponset_id` | CharField(120) | blank |
| `org_id` | IntegerField | db_index; Zoho org ID (integer) |
| `coupon_name` | CharField(255) | blank |
| `coupon_code` | CharField(120) | db_index |
| `description` | TextField | blank |
| `is_active` | BooleanField | default False |
| `status` | CharField(120) | blank |
| `rule_type` | CharField(120) | blank |
| `coupon_type` | CharField(120) | blank; e.g. 'percentage', 'flat', 'free_shipping', 'buyxgety' |
| `show_in_storefront` | BooleanField | default False |
| `restrict_for_guest_user` | BooleanField | |
| `restrict_for_offline_payments` | BooleanField | |
| `stop_after_this_rule` | BooleanField | |
| `apply_once_per_order` | BooleanField | |
| `type` | CharField(120) | blank |
| `duration` | CharField(120) | blank |
| `discount_type` | CharField(120) | blank; e.g. 'percentage', 'fixed_amount' |
| `discount_by` | CharField(120) | blank |
| `apply_on` | CharField(120) | blank |
| `discount_value` | CharField(120) | blank; numeric string |
| `discount_amounts` | JSONField | default list |
| `max_discount_amount` | CharField(120) | blank |
| `max_redemption` | IntegerField | default 0 |
| `max_redemption_count` | IntegerField | default 0 |
| `redemption_count` | IntegerField | default 0 |
| `max_redemption_count_per_user` | IntegerField | default 0 |
| `max_usage_per_transaction` | IntegerField | default 0 |
| `max_discounted_product_count_per_cart` | CharField(120) | blank |
| `minimum_order_value` | DecimalField(15,3) | null, blank |
| `minimum_order_quantity` | CharField(120) | blank |
| `activation_time` | DateTimeField | null, blank |
| `expiry_at` | CharField(120) | blank |
| `expiry_time` | DateTimeField | null, blank |
| `eligible_products` | JSONField | default dict |
| `buy_products` | JSONField | default dict |
| `get_products` | JSONField | default dict |
| `eligible_customers` | JSONField | default dict |
| `eligible_shipping_zones` | JSONField | default dict |
| `raw_data` | JSONField | default dict; full Zoho response |
| `last_synced_at` | DateTimeField | auto_now |
| `created_at` | DateTimeField | auto_now_add |

**Meta:** `db_table = 'offer_coupon'`, `unique_together = ('coupon_id', 'org_id')`, `ordering = ['-last_synced_at', '-created_at']`

**`is_expired() -> bool`:** Returns `True` if `expiry_time` exists and `now() >= expiry_time`.

---

#### Model: `CouponUsageLog`

| Field | Type | Notes |
|---|---|---|
| `user_id` | IntegerField | db_index |
| `coupon` | FK(Coupon, null=True) | SET_NULL, related_name='usage_logs' |
| `coupon_id_str` | CharField(120) | Zoho coupon ID string |
| `coupon_code` | CharField(120) | db_index |
| `org_id` | IntegerField | db_index |
| `order_id` | IntegerField | db_index; local `Order.pk` |
| `discount_amount_applied` | DecimalField(15,3) | |
| `coupon_type` | CharField(120) | blank |
| `discount_type` | CharField(120) | blank |
| `used_at` | DateTimeField | auto_now_add |

**Meta:** `db_table = 'offer_coupon_usage_log'`

---

### 9.2 `offer/services.py`

**Total: 551 lines.**

**`_notify_new_coupon(coupon, org_id)`:**
- Finds `Store` by `zoho_org_id = str(org_id)`.
- Creates `UserNotification` of kind `OFFER` for each active user (skips if already notified for this coupon).
- Sends FCM push to all active+push-enabled device tokens.

**`get_store_org_id(store) -> int`:** Returns `int(store.zoho_org_id)`.

**`get_cart_context(user, store) -> tuple[Cart, list[dict], Decimal]`:**
Returns `(cart, cart_items, subtotal)` where `cart_items` is list of `{name, quantity, unit_price, zoho_product_id, ...}`.

**`get_coupon_for_checkout(store, coupon_code) -> Coupon | None`:**
Looks up active, non-expired `Coupon` matching `coupon_code` and `org_id`.

**`coupon_is_applicable(coupon, user, cart_items, subtotal) -> tuple[bool, str]`:**
Validates coupon against user's cart:
- Not expired
- Active
- Min order value met
- Not restricted to registered users (or user is authenticated)
- Not restricted to online payments (or payment method is online)
- Per-user redemption limit not exceeded (checks `CouponUsageLog`)
- Global redemption limit not exceeded

**`calculate_coupon_discount(coupon, cart_items, subtotal, shipping_amount, currency) -> Decimal`:**
Calculates discount amount based on `coupon_type`:
- `percentage`: `discount_value% of subtotal` (capped by `max_discount_amount`)
- `flat` / `fixed_amount`: fixed `discount_value` (capped by subtotal)
- `free_shipping`: `shipping_amount`
- `buyxgety`: handled separately in view (see `OrderSummaryAPIView`)
- Returns Decimal(0) if unrecognized type

**`get_applicable_coupons_for_store(user, store) -> dict`:**
Returns `{available_coupons, auto_applied_coupons}` — lists of `Coupon` dicts applicable to this store/user.

---

### 9.3 `offer/views.py`

#### `CheckoutCouponsAPIView` — GET `/api/offer/checkout-coupons/?store_id=<id>`
- Permission: IsAuthenticated
- Returns `get_applicable_coupons_for_store(user, store)`

#### `OrderSummaryAPIView` — POST `/api/offer/order-summary/`
- Permission: IsAuthenticated
- Body: `{store_id, vat_percent, coupon_code (optional)}`
- Logic:
  1. Gets cart context (subtotal, items).
  2. Calculates VAT and base_total.
  3. If no `coupon_code`: checks for auto-applied coupon; if none, returns summary without discount.
  4. Resolves coupon, validates applicability.
  5. For `buyxgety` coupons: custom BXGy discount calculation with product price lookup.
  6. For other coupons: `calculate_coupon_discount()`.
  7. Recalculates VAT on `subtotal - discount` when discount > 0.
  8. Returns full breakdown: `{coupon_applied, valid, coupon_code, coupon_name, subtotal, vat_percent, vat_amount, shipping_amount, coupon_discount, total, breakdown, product_details, bxgy_get_item?}`.

---

### 9.4 `offer/urls.py`

```python
urlpatterns = [
    path('checkout-coupons/', CheckoutCouponsAPIView),
    path('order-summary/', OrderSummaryAPIView),
]
```

---

## 10. App: `zoho_integration`

### 10.1 `zoho_integration/models.py`

#### Model: `ZohoCommerceAccount`

Represents one Zoho Commerce merchant account (multi-account setup).

| Field | Type | Notes |
|---|---|---|
| `name` | CharField(100) | Display name |
| `email` | EmailField | unique; Zoho account email |
| `organization_id` | CharField(100) | null, blank; Zoho org ID for this account |
| `accounts_url` | URLField | default `https://accounts.zoho.com`; OAuth token endpoint base |
| `commerce_base_url` | URLField | default `https://commerce.zoho.com` |
| `client_id` | CharField(255) | OAuth client ID |
| `client_secret` | CharField(255) | OAuth client secret |
| `refresh_token` | TextField | OAuth refresh token |
| `is_active` | BooleanField | default True |
| `created_at` | DateTimeField | auto_now_add |

**Meta:** `ordering = ['name']`

**`__str__`:** `f'{self.name} ({self.email})'`

---

### 10.2 `zoho_integration/services.py`

**Purpose:** Multi-account Zoho Commerce API service. Provides token caching and standard request methods for `ZohoCommerceAccount` instances.

**In-process token cache:** `_TOKEN_CACHE: dict[str, tuple[str, float]]` — keyed by account `id` or `email`. Tokens are cached with safety margin of 30 seconds.

**`_request_get_with_retries(url, *, headers, params, timeout, label)`:**
Retries up to 3 times on `ConnectionError` and `Timeout` with 0.6s × attempt backoff. Raises `ZohoIntegrationError` with descriptive message.

**`get_zoho_access_token(account) -> str`:**
1. Check cache; return cached token if still valid.
2. POST to `{account.accounts_url}/oauth/v2/token` with refresh credentials.
3. Cache result with `max(60, expires_in - 30)` second TTL.
4. Return `access_token`.

**`clear_zoho_access_token_cache(account)`:** Removes account's cached token.

**`get_all_zoho_stores(account) -> Any`:**
GET `{base_url}/zs-site/api/v1/index/sites`. Retries once on 401 (clears token cache and re-fetches).

**Class: `ZohoCommerceService`**

Instantiated with a `ZohoCommerceAccount`.

| Method | Description |
|---|---|
| `get_access_token()` | Returns cached/refreshed token |
| `_headers()` | `{Authorization, Content-Type}` |
| `list_stores()` | `get_all_zoho_stores(account)` |
| `list_products(org_id, page, per_page, category_id)` | GET `/store/api/v1/products` |
| `list_products_all_pages(org_id, category_id, per_page, max_pages)` | Paginates until `has_more_page=False` or `max_pages` |
| `get_product_detail(org_id, product_id)` | GET `/store/api/v1/products/{product_id}` |
| `list_categories(org_id, page, per_page)` | GET `/store/api/v1/categories` (per_page max 100) |
| `get_category_detail(org_id, category_id)` | GET `/store/api/v1/categories/{category_id}` |

---

### 10.3 `zoho_integration/views.py`

**Total: 112 KB — largest file in project. Contains 16+ view classes.**

#### `zoho_callback` — GET/POST `/zoho/callback/`

OAuth callback endpoint (stub). [INFER: receives Zoho auth code; not fully implemented for ongoing use]

---

#### `MultiAccountZohoStoreListAPIView` — GET `/zoho/multi/stores/`

Returns all Zoho Commerce stores/organizations accessible to all active `ZohoCommerceAccount` records.

Response shape: list of `{account_id, account_name, account_email, organization_id, stores: [{shop_id, shop_name, domain, ...}]}`.

---

#### `MultiAccountZohoProductListAPIView` — GET `/zoho/multi/accounts/<account_id>/products/<organization_id>/`

Products for one account + org. Paginated.

---

#### `MultiAccountZohoProductListQueryAPIView` — GET `/zoho/multi/products/`

Query-param based product list across accounts.

Query params: `organization_id`, `account_id` (optional), `category_id`, `page`, `per_page`, `search`.

---

#### `MultiAccountZohoProductSearchAPIView` — GET `/zoho/multi/products/search/`

Searches products across one or all accounts by name/sku.

---

#### `MultiAccountZohoProductDetailQueryAPIView` — GET `/zoho/multi/product-detail/`

Query params: `organization_id`, `product_id` (Zoho product/variant ID).

---

#### `MultiAccountZohoCategoryListQueryAPIView` — GET `/zoho/multi/categories/`

Lists all Zoho Commerce categories for a given organization.

Query params: `organization_id`, `account_id` (optional), `page`, `per_page`.

---

#### `MultiAccountZohoCategoryListAonegtGroceryQueryAPIView` — GET `/zoho/multi/categories/aonegt-grocery/`

[INFER: A specialized category listing view for a "AoneGT Grocery" store — likely filters to a specific set of categories or org. The exact implementation is in the 112KB views.py.]

---

#### `MultiAccountZohoSubCategoryListQueryAPIView` — GET `/zoho/multi/subcategories/`

Lists subcategories (children of a parent category).

---

#### `MultiAccountZohoCategorySearchAPIView` — GET `/zoho/multi/categories/search/`

Searches categories by name.

---

#### `MultiAccountZohoCategoryImageProxyAPIView` — GET `/zoho/multi/accounts/<account_id>/categories/<organization_id>/<category_id>/image/`

Proxies a category image from Zoho CDN. Uses `document_id` from category detail to build CDN URL.

---

#### `MultiAccountZohoCategoryImageQueryAPIView` — GET `/zoho/multi/categories/image/`

Query-param based category image proxy.

---

#### `MultiAccountZohoBestDealsAPIView` — GET `/zoho/multi/best-deals/`

Returns best-deal products. Two sources:
1. **`source=admin`:** Local `Product` rows where `is_best_deal=True`, ordered by `best_deal_sort_order`. Enriches with live price from Zoho.
2. **Default (Zoho live):** Fetches all products from active accounts, applies a "best deals" filter.

---

#### `MultiAccountZohoCollectionListQueryAPIView` — GET `/zoho/multi/collections/`

Lists Zoho Commerce collections for a given organization.

---

**Key helper functions exported from `zoho_integration/views.py`** (used by `catalog/views.py`):
- `_extract_image_url(product: dict) -> str` — Extracts `image_url` or `image_name` from Zoho product dict.
- `_extract_price(product: dict) -> str` — Extracts `rate` or `price` from Zoho product dict.
- `_product_summary(product: dict, *, store_domain: str) -> dict` — Builds a normalized product summary dict.
- `build_image_url(store_domain: str, image_name_or_url: str) -> str` — Builds full CDN URL from a Zoho image name/document_id.

---

### 10.4 `zoho_integration/urls.py`

```python
urlpatterns = [
    path('callback/', zoho_callback),
    path('multi/stores/', MultiAccountZohoStoreListAPIView),
    path('multi/products/', MultiAccountZohoProductListQueryAPIView),
    path('multi/products/search/', MultiAccountZohoProductSearchAPIView),
    path('multi/best-deals/', MultiAccountZohoBestDealsAPIView),
    path('multi/collections/', MultiAccountZohoCollectionListQueryAPIView),
    path('multi/product-detail/', MultiAccountZohoProductDetailQueryAPIView),
    path('multi/categories/aonegt-grocery/', MultiAccountZohoCategoryListAonegtGroceryQueryAPIView),
    path('multi/categories/', MultiAccountZohoCategoryListQueryAPIView),
    path('multi/subcategories/', MultiAccountZohoSubCategoryListQueryAPIView),
    path('multi/categories/search/', MultiAccountZohoCategorySearchAPIView),
    path('multi/categories/image/', MultiAccountZohoCategoryImageQueryAPIView),
    path('multi/accounts/<int:account_id>/products/<str:organization_id>/', MultiAccountZohoProductListAPIView),
    path('multi/accounts/<int:account_id>/categories/<str:organization_id>/<str:category_id>/image/', MultiAccountZohoCategoryImageProxyAPIView),
]
```

---

### 10.5 `zoho_integration/commerce_collections.py`

**Purpose:** Helpers for fetching Zoho Commerce collections via the store API.

Key function: `get_zoho_commerce_collections(account, organization_id, page, per_page) -> dict`
- GET `/store/api/v1/collections` with org header.
- Returns Zoho JSON.

---

### 10.6 `zoho_integration/storefront_collections.py`

**Purpose:** Helpers for fetching Zoho storefront collections (uses `domain-name` header instead of org header).

Key function: `get_zoho_storefront_collections(store_domain, page, per_page) -> dict`
- GET `{base}/storefront/api/v1/collections` with `domain-name` header.

---

## 11. App: `superuser`

### 11.1 `superuser/views.py`

**`create_superuser(request)`** — POST `/api/superuser/create-superuser/`

Security: Checks `X-ADMIN-SECRET` header against `settings.SUPERUSER_API_SECRET`. Rejects with 403 if missing or wrong.

Input: `{email, password}`

Logic:
1. Validate `email` and `password` present.
2. Check `User.objects.filter(email=email).exists()` — return 400 if already exists.
3. `User.objects.create_superuser(email=email, password=password)`.
4. Return 201 `{"message": "Superuser created successfully"}`.

Error handling: `IntegrityError` → 400, any `Exception` → 500.

**Use case:** One-time superuser bootstrap on a fresh deployment without shell access.

---

## 12. Cross-App Data Flow & Integration Patterns

### 12.1 User Registration Flow

```
Client POST /api/accounts/register/
  → [optional] check_zoho_contact_exists(email) [accounts.services]
      → Zoho Inventory/Books contact API
  → Create User (is_active=False initially) [INFER]
  → Generate OTPRecord(purpose='register')
  → Send OTP email

Client POST /api/accounts/verify-registration/ {otp_code}
  → Verify OTP → set is_active=True
  → post_save signal → welcome_member_notification [shop.signals]
  → Return JWT tokens
```

### 12.2 Checkout Flow

```
Client POST /api/shop/orders/checkout/ {store_id, address_id, payment_method, ...}
  → CheckoutSerializer.validate()
      → Resolve Store, Cart, Address
      → Validate loyalty coupon or points_to_redeem
      → Validate payment_success for prepaid methods
  → Create Order + OrderItems (atomic)
  → Clear CartItems for this store
  → Award loyalty points → PurchasePointsLedger
  → Send order confirmation email [INFER]
  → Push FCM notification [INFER]
  → maybe_create_zoho_sales_order_for_order(order_id) [shop.services.zoho_sales_order]
      → ZohoCommerceService.admin_post('salesorders', body, store=store)
  → Return OrderSerializer data
```

### 12.3 Zoho Books Invoice Flow

```
Staff POST /api/shop/orders/<pk>/zoho-books/invoice/
  → Resolve Order (must be SYNCED)
  → get_or_create_zoho_books_contact(order) [shop.services.zoho_books]
  → create_zoho_books_invoice_for_order(order) [shop.services.zoho_books_invoice]
      → Build invoice body
      → Apply credit if credit_applied_on_invoice > 0
      → POST to Zoho Books /invoices
  → Update Order: zoho_books_invoice_id, zoho_books_invoice_number, zoho_books_invoiced_at
  → Return updated OrderSerializer
```

### 12.4 Product Sync Flow

```
python manage.py sync_zoho_products --all-stores

For each Store:
  → sync_store_from_zoho(store) [catalog.services.zoho_product_sync]
      → Paginate GET /store/api/v1/products?filter_by=Status.Active
      → For each product:
          → expand_zoho_list_product(raw) → list of rows (one per variant)
          → _upsert_product(store, row) → create/update/unchanged Product
      → Report stats
```

### 12.5 Coupon Sync & Notification Flow

```
[External trigger or management command]
  → Sync coupons from Zoho Commerce to offer.Coupon table
  → For each new coupon:
      → _notify_new_coupon(coupon, org_id) [offer.services]
          → Find Store by zoho_org_id
          → For each active User: create UserNotification (kind=OFFER)
          → Send FCM push to all active device tokens
```

### 12.6 Geidea Payment Flow

```
Phase 1 — Order Placement (Checkout):
Client POST /api/shop/orders/checkout/ {store_id, payment_method='payment_gateway', ...}
  → CheckoutSerializer.validate()
      → No payment_success or gateway_reference required at checkout stage.
  → Create local Order (status='pending_zoho_sync', payment_status='pending', total)
  → maybe_create_zoho_books_sales_order_for_order(order_id)
      → Creates Sales Order in Zoho Books
      → Stores zoho_books_salesorder_id on Order
  → Return OrderSerializer data

Phase 2 — Initiate Geidea Session:
Client POST /api/shop/geidea/initiate/ {order_id}
  → Verify order belongs to requesting user, status is not CANCELLED, payment_status is not PAID.
  → Guard: zoho_books_salesorder_id must be populated (if not, return 400 for retry).
  → create_geidea_session(order) [shop.services.geidea]
      → Call Geidea Create Session API server-to-server (amount, currency, merchantReferenceId=zoho_books_salesorder_id)
      → Return session_id to frontend
  → Frontend launches Geidea hosted payment page (HPP) using session_id
```

---

## 13. API Endpoint Reference (Complete)

### Auth & Accounts — `/api/accounts/`

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/accounts/register/` | None | Register new user |
| POST | `/api/accounts/verify-registration/` | None | Verify registration OTP |
| POST | `/api/accounts/login/` | None | Login, get JWT pair |
| POST | `/api/accounts/send-otp/` | None | Send OTP (any purpose) |
| POST | `/api/accounts/verify-otp/` | None | Verify OTP (generic) |
| POST | `/api/accounts/password-reset-request/` | None | Send password-reset OTP |
| POST | `/api/accounts/password-reset/` | None | Reset password with OTP |
| POST | `/api/accounts/token/refresh/` | None | Refresh JWT access token |
| GET/PATCH | `/api/accounts/profile/` | JWT | Read/update user profile |
| POST | `/api/accounts/change-password/` | JWT | Change password |
| POST | `/api/accounts/report-issue/` | JWT | Submit issue report |
| POST | `/api/accounts/deactivate/` | JWT | Deactivate account |
| POST | `/api/accounts/delete/` | JWT | Delete account |
| POST | `/api/accounts/reactivate/` | None | Reactivate account with OTP |
| GET | `/api/accounts/credit-balance/` | JWT | Get prepaid AED credit balance |
| GET | `/api/accounts/loyalty-points/` | JWT | Get loyalty points total |

### Catalog — `/api/catalog/`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/catalog/banners/` | None | Active banners (optional `?store_id=`) |
| GET/POST | `/api/catalog/admin/banners/` | Admin | List/create banners |
| GET/PATCH/DELETE | `/api/catalog/admin/banners/<pk>/` | Admin | Banner detail |
| GET | `/api/catalog/zoho/shops/` | JWT | Zoho shop list (from ZohoCommerceAccount) |
| GET | `/api/catalog/zoho/shops/<shop_id>/products/` | JWT | Products for one Zoho shop |
| GET | `/api/catalog/zoho-commerce/products/` | JWT | Proxy Zoho Commerce product list |
| GET | `/api/catalog/zoho-commerce/products/<product_id>/` | JWT | Proxy Zoho Commerce product detail |
| GET/POST | `/api/catalog/admin/stores/` | Admin | Store list/create |
| GET/PATCH/DELETE | `/api/catalog/admin/stores/<pk>/` | Admin | Store detail |
| GET/POST | `/api/catalog/admin/stores/<store_id>/products/` | Admin | Store product list/create |
| GET/PATCH/DELETE | `/api/catalog/admin/stores/<store_id>/products/<pk>/` | Admin | Store product detail |
| GET | `/api/catalog/stores/` | JWT | Active store list |
| GET/POST | `/api/catalog/stores/products/reviews/` | GET: None / POST: JWT | Reviews for a product (by `?store_id=&zoho_product_id=`) |
| GET | `/api/catalog/stores/products/rating/` | None | Rating summary for a product |
| GET | `/api/catalog/stores/<store_id>/products/` | JWT | Products for one store (paginated, searchable) |
| GET | `/api/catalog/stores/<store_id>/products/<pk>/` | JWT | Product detail |
| GET | `/api/catalog/products/related/` | JWT | Related product suggestions |

### Shop — `/api/shop/`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET/POST | `/api/shop/addresses/` | JWT | Address list/create |
| GET/PATCH/DELETE | `/api/shop/addresses/<pk>/` | JWT | Address detail |
| GET/POST | `/api/shop/wishlist/` | JWT | Wishlist list/add |
| GET/PATCH/DELETE | `/api/shop/wishlist/item/` | JWT | Wishlist item (by query params) |
| POST | `/api/shop/wishlist/move-to-cart/` | JWT | Move wishlist item to cart |
| GET | `/api/shop/cart/` | JWT | Cart detail (all items grouped) |
| GET | `/api/shop/cart/summary/` | JWT | Cart summary (totals by store) |
| POST | `/api/shop/cart/clear/` | JWT | Clear cart |
| POST | `/api/shop/cart/items/` | JWT | Add/update cart item |
| POST | `/api/shop/orders/checkout/` | JWT | Create order from cart |
| POST | `/api/shop/geidea/initiate/` | JWT | Initiate Geidea payment session |
| GET | `/api/shop/rewards/points/` | JWT | Loyalty points balance |
| POST | `/api/shop/rewards/issue-coupon/` | JWT | Issue loyalty coupon (spend points) |
| GET | `/api/shop/orders/return-flow/` | JWT | Return flow UI metadata |
| GET/POST | `/api/shop/orders/returns/` | JWT | Returns for an order (by `?order_id=`) |
| GET/POST | `/api/shop/orders/<pk>/returns/` | JWT | Returns for order by pk |
| POST | `/api/shop/orders/reorder/` | JWT | Re-add order items to cart |
| GET | `/api/shop/orders/` | JWT | Order history list |
| PATCH | `/api/shop/orders/confirm/` | JWT/Admin | Confirm/update order (by query) |
| GET | `/api/shop/orders/detail/` | JWT | Order detail (by `?order_id=`) |
| POST | `/api/shop/orders/<pk>/zoho-books/invoice/` | Admin | Trigger Zoho Books invoice |
| POST | `/api/shop/orders/<pk>/zoho-books/payment/` | Admin | Trigger Zoho Books payment |
| POST | `/api/shop/orders/<pk>/zoho-books/cancel/` | Admin | Cancel Zoho Books records |
| POST | `/api/shop/orders/<pk>/payment-success/` | JWT | Mark gateway/paylink payment success |
| GET | `/api/shop/orders/<pk>/` | JWT | Order detail by pk |
| GET | `/api/shop/notifications/unread-count/` | JWT | Unread notification count |
| POST | `/api/shop/notifications/mark-all-read/` | JWT | Mark all notifications read |
| GET | `/api/shop/notifications/offers/` | JWT | Offer notifications with coupon detail |
| GET/PATCH | `/api/shop/notifications/<pk>/` | JWT | Notification detail / mark read |
| GET | `/api/shop/notifications/` | JWT | All notifications (paginated) |
| POST | `/api/shop/devices/register/` | JWT | Register FCM device token |
| POST | `/api/shop/devices/unregister/` | JWT | Unregister FCM device token |
| PATCH | `/api/shop/notifications/push-settings/` | JWT | Update push_enabled setting |
| GET | `/api/shop/zoho-products/` | JWT | Live Zoho product list (storefront API) |
| GET | `/api/shop/zoho-products/<product_id>/` | JWT | Live Zoho product detail |
| GET | `/api/shop/zoho-products/<product_id>/image/` | None | Product image proxy (for image URLs) |

### Offer — `/api/offer/`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/offer/checkout-coupons/?store_id=<id>` | JWT | Applicable coupons for checkout |
| POST | `/api/offer/order-summary/` | JWT | Order summary with coupon discount preview |

### Superuser — `/api/superuser/`

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/superuser/create-superuser/` | X-ADMIN-SECRET header | Create superuser account |

### Zoho Integration — `/zoho/`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET/POST | `/zoho/callback/` | None | Zoho OAuth callback |
| GET | `/zoho/multi/stores/` | JWT | All Zoho stores from all accounts |
| GET | `/zoho/multi/products/` | JWT | Products by `?organization_id=` |
| GET | `/zoho/multi/products/search/` | JWT | Product search |
| GET | `/zoho/multi/best-deals/` | JWT | Best deal products |
| GET | `/zoho/multi/collections/` | JWT | Collections by `?organization_id=` |
| GET | `/zoho/multi/product-detail/` | JWT | Product detail by `?organization_id=&product_id=` |
| GET | `/zoho/multi/categories/aonegt-grocery/` | JWT | AoneGT Grocery categories |
| GET | `/zoho/multi/categories/` | JWT | Categories by `?organization_id=` |
| GET | `/zoho/multi/subcategories/` | JWT | Subcategories |
| GET | `/zoho/multi/categories/search/` | JWT | Category search |
| GET | `/zoho/multi/categories/image/` | None | Category image proxy (by query) |
| GET | `/zoho/multi/accounts/<id>/products/<org_id>/` | JWT | Products for one account+org |
| GET | `/zoho/multi/accounts/<id>/categories/<org_id>/<category_id>/image/` | None | Category image proxy |

---

## 14. Background Jobs & Scheduled Tasks

### 14.1 APScheduler — OTP Cleanup

**File:** `accounts/scheduler.py`

**Trigger:** Called from `accounts/apps.py` `AccountsConfig.ready()`.

**Deduplication mechanism:** File lock at `.otp_purge_scheduler.lock`. Only one Gunicorn worker will succeed in acquiring the lock.

**Job:** Purges expired/used OTPRecord rows periodically (interval from settings, likely every few minutes or hourly).

**Shutdown:** `atexit` handler calls `scheduler.shutdown()`.

### 14.2 Management Commands (Manual / Cron)

| Command | Description |
|---|---|
| `python manage.py purge_expired_otps` | One-shot OTP purge |
| `python manage.py sync_zoho_products --all-stores` | Sync Zoho Commerce products to local DB |
| `python manage.py sync_zoho_products --store-id <pk>` | Sync one store |
| `python manage.py sync_zoho_products --dry-run` | Dry-run product sync |

---

## 15. Zoho Integration Architecture

### 15.1 Three Distinct Zoho OAuth Patterns

| Pattern | Where Used | Token Source | Headers |
|---|---|---|---|
| **Static token (urllib)** | `catalog.services.zoho_commerce_products`, `shop.services.zoho_commerce` (module-level functions) | `ZOHO_ACCESS_TOKEN` env | `Authorization: Zoho-oauthtoken {token}`, `X-com-zoho-store-organizationid: {org}` |
| **Per-store OAuth refresh** | `shop.services.zoho_commerce.ZohoCommerceService`, `catalog.services.zoho_sites` | `Store.refresh_token/client_id/client_secret` → `Store.access_token` (persisted with expiry); falls back to `ZohoCommerceAccount`; then global settings | Admin API: `Authorization`, `X-com-zoho-store-organizationid`. Storefront API: `domain-name` header |
| **Per-account OAuth** | `zoho_integration.services.ZohoCommerceService` | `ZohoCommerceAccount.refresh_token/client_id/client_secret`; in-process `_TOKEN_CACHE` | `Authorization: Zoho-oauthtoken {token}` |

### 15.2 Zoho API Surfaces Used

| Zoho Surface | Endpoint Base | Usage |
|---|---|---|
| Commerce Admin Store API | `commerce.zoho.com/store/api/v1/` | Sales orders, products (admin), categories |
| Commerce Storefront API | `commerce.zoho.com/storefront/api/v1/` | Products (storefront), product detail, collections |
| Commerce Sites Index | `commerce.zoho.com/zs-site/api/v1/index/sites` | List stores/shops |
| Zoho Books API | `books.zoho.com/api/v3/` | Contacts, invoices, sales orders, payments |
| Zoho OAuth | `accounts.zoho.com/oauth/v2/token` | Token refresh |
| Zoho Inventory | [INFER from `accounts.services`] | Contact existence check for registration gate |

### 15.3 Zoho Data Flow in Orders

```
Order created locally (shop.Order)
    ↓
[if ZOHO_COMMERCE_CREATE_SALES_ORDER_ENABLED]
    → Zoho Commerce: POST /store/api/v1/salesorders
    → Store zoho_salesorder_id on Order
    ↓
[Staff action: POST /api/shop/orders/<pk>/zoho-books/invoice/]
    → Zoho Books: GET/POST /contacts (get_or_create_zoho_books_contact)
    → Zoho Books: POST /invoices
    → Store zoho_books_invoice_id on Order
    ↓
[Staff action: POST /api/shop/orders/<pk>/zoho-books/payment/]
    → Zoho Books: POST /customerpayments
    → Store zoho_books_payment_id on Order
```

---

## 16. Auth & Security Model

### 16.1 Authentication

- **JWT Bearer tokens** via `djangorestframework-simplejwt`.
- Default header: `Authorization: Bearer <access_token>`.
- Access token lifetime: `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` env (default 60 min).
- Refresh token lifetime: `JWT_REFRESH_TOKEN_LIFETIME_DAYS` env (default 7 days).
- Refresh endpoint: `POST /api/accounts/token/refresh/`.

### 16.2 Authorization Levels

| Level | Meaning | DRF Permission |
|---|---|---|
| Public | No auth required | `AllowAny` |
| Authenticated | Valid JWT required | `IsAuthenticated` |
| Admin/Staff | `user.is_staff=True` | `IsAdminUser` |
| Superuser | `user.is_superuser=True` | `IsSuperUser` or custom |

### 16.3 OTP Security

- OTP codes: 6-digit numeric.
- Per-purpose: each OTP purpose (`register`, `reset_password`, etc.) creates its own `OTPRecord`.
- Cooldown: `OTP_COOLDOWN_SECONDS` — enforced via `OTPRequestThrottle`.
- Max attempts: `OTP_MAX_ATTEMPTS` — OTP becomes invalid after N failed attempts.
- Expiry: `OTP_EXPIRY_MINUTES`.
- Used OTPs: `is_used=True` after successful verification — prevents replay.

### 16.4 Superuser API Secret

`POST /api/superuser/create-superuser/` requires `X-ADMIN-SECRET: {SUPERUSER_API_SECRET}`. This is a string secret stored in env, not a JWT. 403 returned immediately if wrong.

---

## 17. Loyalty System

### 17.1 Points Earning

- **When:** On successful checkout (order creation).
- **Rate:** 1 point per `LOYALTY_AED_PER_POINT_EARNED` AED (default: 100 AED = 1 point) of final paid total (after loyalty discount applied).
- **Currency gate:** Only applies to AED orders.
- **Record:** `PurchasePointsLedger` (one per order, OneToOne).

### 17.2 Points Redemption

**Method 1 — Issue Coupon:**
- `POST /api/shop/rewards/issue-coupon/ {points: N}`
- Minimum: `LOYALTY_MIN_POINTS_TO_REDEEM` (default 100).
- Converts points to AED at `LOYALTY_POINT_VALUE_AED` (default 1 AED/point).
- Creates `LoyaltyIssuedCoupon` with unique code, amount, expiry.
- Deducts `N` points from user's balance (via `PurchasePointsLedger` deduction entry).
- Returns coupon `code` to use at checkout.

**Method 2 — Direct at Checkout:**
- `POST /api/shop/orders/checkout/ {points_to_redeem: N}`
- Validates max redeemable ≤ `max_points_redeemable_for_total(order_total, point_value_aed())`.
- Applies `loyalty_discount = N * point_value_aed()` to order.
- Records `loyalty_points_redeemed` + `loyalty_discount` on Order.

### 17.3 Points Balance

- Total points = sum of all `PurchasePointsLedger.points_awarded` for this user.
- Negative entries (deductions) can exist for coupon issuance. [INFER]
- Endpoint: `GET /api/accounts/loyalty-points/` → returns current balance.

---

## 18. Notification System

### 18.1 In-App Notifications

**Model:** `shop.UserNotification` (kind, title, body, payload, read_at).

**Kinds:**
- `offer` — New coupon/offer (links to `Coupon.coupon_id` in `payload`)
- `order` — Order status update
- `points_reward` — Loyalty points awarded
- `points_deducted` — Loyalty points deducted
- `member_offer` — Welcome notification (on registration)

**APIs:**
- `GET /api/shop/notifications/` — All notifications, paginated
- `GET /api/shop/notifications/offers/` — Offer-kind notifications with coupon detail enrichment
- `GET /api/shop/notifications/<pk>/` — Single notification (marks read on PATCH)
- `GET /api/shop/notifications/unread-count/` — Count of unread
- `POST /api/shop/notifications/mark-all-read/` — Mark all read

### 18.2 Push Notifications

**Provider:** Firebase Cloud Messaging (FCM) via `firebase-admin` SDK.

**Device registration:**
- `POST /api/shop/devices/register/ {token, device_type}` — Creates `FCMDeviceToken`
- `POST /api/shop/devices/unregister/` — Deactivates token
- `PATCH /api/shop/notifications/push-settings/ {push_enabled: bool}` — Toggle push

**Sending:** `shop.services.push_notifications.send_push_notification(tokens, title, body, data, expanded_body)`:
- Uses `firebase_admin.messaging.MulticastMessage`.
- All `data` values must be strings.
- Fires and forgets (errors logged, not raised).

---

## 19. Project Status & Known Patterns

### 19.1 Patterns & Conventions

1. **Multi-store architecture:** Every resource (Cart items, Orders, Wishlist items) carries a `store` FK. Checkout is always per-store (one store at a time from cart).

2. **Zoho ID as primary identifier:** Products are keyed by `zoho_product_id` (variant_id when variants exist). This ID maps to Zoho Commerce items and is used in sales orders, invoice line items, and returns.

3. **"By query param" endpoints:** Many endpoints have two URL patterns — one with a PK in the URL and one with a query param (e.g., `orders/<pk>/` and `orders/detail/?order_id=<pk>`). This is for mobile client flexibility.

4. **Best-effort Zoho sync:** All Zoho calls from checkout/order flows are wrapped in try/except with logging. A Zoho outage does not block order creation. Orders are created locally first, Zoho sync is secondary.

5. **OAuth token persistence on Store:** When `ZohoCommerceService.refresh_access_token(store)` refreshes a token, it saves `Store.access_token` + `Store.token_expiry` to avoid redundant refresh calls across requests.

6. **In-process token cache in `zoho_integration`:** `_TOKEN_CACHE` dict in `zoho_integration/services.py` caches tokens per account. This is process-local (not shared between Gunicorn workers) — each worker has its own cache.

7. **Serializer context patterns:** Views pass `request` in serializer context for URL building. Some serializers also receive `product` or `order` in context for cross-model validation.

8. **Snapshot fields on OrderItem:** `product_name`, `sku`, `unit_price`, `line_total` are snapshot values at order time. The `product` FK can be NULL (product deleted), but these snapshots preserve order history integrity.

9. **Return eligibility gate:** Returns require `order.status == SYNCED` OR `customer_tracking_stage == delivered`. Business logic enforced in both serializer and view.

10. **Prepaid flow (payment_gateway / pay_by_link):**
    - For `pay_by_link`: Client must complete payment externally, then send `payment_success=True + gateway_reference` at checkout. Server validates this and credits `UserCreditBalance` with the paid amount, then deducts when creating the Zoho Books invoice.
    - For `payment_gateway` (Geidea): Checkout does not require payment success. Order is created in `pending_zoho_sync` status, synced to Zoho Books to obtain `zoho_books_salesorder_id`, and then a Geidea session is initiated via `/api/shop/geidea/initiate/` using the Zoho Books Sales Order ID as `merchantReferenceId`. The payment is confirmed later via callback or status polling.

### 19.2 Feature Flags

| Flag | Default | Effect |
|---|---|---|
| `ZOHO_COMMERCE_CREATE_SALES_ORDER_ENABLED` | False | Enable/disable Zoho Commerce sales order creation on checkout |
| `REGISTRATION_REQUIRE_ZOHO_CONTACT` | False | Gate registration on Zoho contact existence |
| `CHECKOUT_REQUIRE_PREPAID_PAYMENT_SUCCESS` | True | Require `payment_success=True` for gateway/paylink |
| `CHECKOUT_TRUST_CLIENT_SHIPPING` | False | If True, shipping in OrderSummary is always 0 |

### 19.3 Known Implementation Notes

- `zoho_integration/views.py` is 112 KB — the single largest file. It contains all multi-account Zoho proxy views and shared helper functions (`_extract_image_url`, `_extract_price`, `_product_summary`, `build_image_url`).
- `shop/services/zoho_returns.py` is only 206 bytes — return sync to Zoho is not yet fully implemented (stub).
- `offer/views_internal_temp.py` (1886 bytes) is a temporary/internal view file — likely experimental endpoints not included in production URLs.
- The `Cart` model enforces one cart per user via a UniqueConstraint. Cart items are NOT deleted on checkout — only items for the specific store being checked out are removed.
- `StoreListAPIView` permission: The `queryset` uses `filter(is_active=True)` but no explicit `permission_classes` is set on the class — it inherits `DEFAULT_PERMISSION_CLASSES = [IsAuthenticated]` from REST_FRAMEWORK settings.

---

*End of PROJECT_TRUTH.md — This document is the single, complete, authoritative source of truth for the AoneGT Backend project.*

---

## Auto Sync Update — 2026-06-06 (INSTRUCTION.md Re-run)

The following files were detected as created or modified in the current session and are recorded here verbatim to keep PROJECT_TRUTH.md in sync with the workspace (migration files are intentionally excluded from this list).

Files changed:
- `shop/serializers.py` (modified)
- `shop/urls.py` (modified)
- `shop/views.py` (modified)
- `shop/api_delivery_zones.py` (created)
- `shop/services/delivery_zones.py` (created)

-----------------------------------------------------------------

### `offer/views.py` — Full Source Code
```python
from decimal import Decimal

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Product, Store
from shop.services.delivery_zones import get_shipping_fee

from .models import Coupon
from .serializers import OrderSummaryRequestSerializer, StoreIdQuerySerializer
from .services import (
    _as_decimal,
    calculate_coupon_discount,
    coupon_is_applicable,
    get_applicable_coupons_for_store,
    get_cart_context,
    get_coupon_for_checkout,
)


class CheckoutCouponsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        ser = StoreIdQuerySerializer(data=request.query_params)
        ser.is_valid(raise_exception=True)
        store = Store.objects.get(pk=ser.validated_data['store_id'], is_active=True)
        return Response(get_applicable_coupons_for_store(request.user, store), status=status.HTTP_200_OK)


class OrderSummaryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = OrderSummaryRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        store = Store.objects.get(pk=ser.validated_data['store_id'], is_active=True)
        vat_percent = Decimal(ser.validated_data['vat_percent']).quantize(Decimal('0.01'))
        coupon_code = (ser.validated_data.get('coupon_code') or '').strip()
        _cart, cart_items, subtotal = get_cart_context(request.user, store)
        city = request.data.get('city', '')
        payment_method = request.data.get('payment_method', 'cash_on_delivery')
        shipping_amount = get_shipping_fee(city, subtotal, payment_method)
        if getattr(settings, 'CHECKOUT_TRUST_CLIENT_SHIPPING', False):
            shipping_amount = Decimal('0.00')
        vat_amount = ((subtotal * vat_percent) / Decimal('100')).quantize(Decimal('0.01'))
        base_total = (subtotal + vat_amount + shipping_amount).quantize(Decimal('0.01'))
        product_details = {
            item['name']: {
                'count': item['quantity'],
                'price': float((item['unit_price'] * item['quantity']).quantize(Decimal('0.01')))
            }
            for item in cart_items
            if item.get('name')
        }
        breakdown = [
            {'label': 'Subtotal', 'value': subtotal},
            {'label': f'VAT ({vat_percent})', 'value': vat_amount},
            {'label': 'Shipping', 'value': shipping_amount},
        ]

        coupon = None
        if not coupon_code:
            applicable = get_applicable_coupons_for_store(request.user, store)
            auto_coupons = applicable.get('auto_applied_coupons') or []
            first_auto = auto_coupons[0] if isinstance(auto_coupons, list) and auto_coupons else None
            if isinstance(first_auto, dict):
                auto_coupon_id = str(first_auto.get('coupon_id') or '').strip()
                if auto_coupon_id:
                    org_raw = (getattr(store, 'zoho_org_id', '') or getattr(settings, 'ZOHO_COMMERCE_ORGANIZATION_ID', '')).strip()
                    try:
                        org_id = int(org_raw)
                    except Exception:
                        org_id = None
                    coupon_qs = Coupon.objects.filter(coupon_id=auto_coupon_id)
                    if org_id is not None:
                        coupon_qs = coupon_qs.filter(org_id=org_id)
                    coupon = coupon_qs.first()
            if coupon is None:
                breakdown.append({'label': 'Total', 'value': base_total})
                return Response(
                    {
                        'coupon_applied': False,
                        'valid': True,
                        'subtotal': subtotal,
                        'vat_percent': str(vat_percent),
                        'vat_amount': vat_amount,
                        'shipping_amount': shipping_amount,
                        'coupon_discount': Decimal('0.00'),
                        'total': base_total,
                        'breakdown': breakdown,
                        'product_details': product_details,
                    },
                    status=status.HTTP_200_OK,
                )

        if coupon is None:
            coupon = get_coupon_for_checkout(store, coupon_code)
        if coupon_code and coupon is None:
            return Response(
                {
                    'coupon_applied': False,
                    'valid': False,
                    'error': 'Coupon not found',
                    'subtotal': subtotal,
                    'vat_percent': str(vat_percent),
                    'vat_amount': vat_amount,
                    'shipping_amount': shipping_amount,
                    'coupon_discount': Decimal('0.00'),
                    'total': base_total,
                    'breakdown': breakdown + [{'label': 'Total', 'value': base_total}],
                    'product_details': product_details,
                },
                status=status.HTTP_200_OK,
            )

        allowed, reason = coupon_is_applicable(coupon, request.user, cart_items, subtotal)
        if not allowed:
            return Response(
                {
                    'coupon_applied': False,
                    'valid': False,
                    'error': reason,
                    'subtotal': subtotal,
                    'vat_percent': str(vat_percent),
                    'vat_amount': vat_amount,
                    'shipping_amount': shipping_amount,
                    'coupon_discount': Decimal('0.00'),
                    'total': base_total,
                    'breakdown': breakdown + [{'label': 'Total', 'value': base_total}],
                    'product_details': product_details,
                },
                status=status.HTTP_200_OK,
            )

        bxgy_get_item = None
        if (coupon.coupon_type or '').lower() == 'buyxgety':
            get_products = coupon.get_products if isinstance(coupon.get_products, dict) else {}
            get_product_rows = get_products.get('products', []) if isinstance(get_products, dict) else []
            get_qty = float(get_products.get('quantity') or 1) if isinstance(get_products, dict) else 1.0
            max_count = float(coupon.max_discounted_product_count_per_cart or get_qty)
            max_discount_amount = _as_decimal(coupon.max_discount_amount or '0') if coupon.max_discount_amount else Decimal('0')
            discount = Decimal('0.00')
            for product_row in get_product_rows if isinstance(get_product_rows, list) else []:
                if not isinstance(product_row, dict):
                    continue
                zoho_product_id = str(product_row.get('product_id') or '').strip()
                if not zoho_product_id:
                    continue
                product = Product.objects.filter(store=store, zoho_product_id=zoho_product_id).first()
                if product is None:
                    continue
                get_unit_price = product.price
                get_line_total = (get_unit_price * Decimal(str(max_count))).quantize(Decimal('0.01'))
                discount = (get_line_total * _as_decimal(coupon.discount_value or '0') / Decimal('100')).quantize(Decimal('0.01'))
                if max_discount_amount > Decimal('0'):
                    discount = min(discount, max_discount_amount)
                bxgy_get_item = {
                    'name': product.name,
                    'quantity': int(max_count),
                    'unit_price': str(get_unit_price.quantize(Decimal('0.01'))),
                    'line_total': str(get_line_total.quantize(Decimal('0.01'))),
                    'discount': str(discount.quantize(Decimal('0.01'))),
                    'zoho_product_id': zoho_product_id,
                }
                break
        else:
            discount = calculate_coupon_discount(coupon, cart_items, subtotal, shipping_amount, 'AED')
        if discount > Decimal('0.00'):
            taxable_amount = subtotal - discount
            vat_amount = (taxable_amount * vat_percent / Decimal('100')).quantize(Decimal('0.01'))
            final_total = (taxable_amount + vat_amount + shipping_amount).quantize(Decimal('0.01'))
            if final_total < Decimal('0'):
                final_total = Decimal('0.00')
        else:
            final_total = (base_total - discount).quantize(Decimal('0.01'))
            if final_total < Decimal('0'):
                final_total = Decimal('0.00')
        breakdown = [
            {'label': 'Subtotal', 'value': subtotal},
            {'label': f'Coupon Discount ({coupon.coupon_code})', 'value': -discount},
            {'label': f'VAT ({vat_percent})', 'value': vat_amount},
            {'label': 'Shipping', 'value': shipping_amount},
            {'label': 'Total', 'value': final_total},
        ]
        response_data = {
            'coupon_applied': True,
            'valid': True,
            'coupon_code': coupon.coupon_code,
            'coupon_name': coupon.coupon_name,
            'coupon_type': coupon.coupon_type,
            'subtotal': subtotal,
            'vat_percent': str(vat_percent),
            'vat_amount': vat_amount,
            'shipping_amount': 'FREE' if (coupon.coupon_type or '').lower() == 'free_shipping' else shipping_amount,
            'coupon_discount': discount,
            'total': final_total,
            'breakdown': breakdown,
            'product_details': product_details,
        }
        if bxgy_get_item is not None:
            response_data['bxgy_get_item'] = bxgy_get_item
        return Response(response_data, status=status.HTTP_200_OK)
```

-----------------------------------------------------------------

### `shop/admin.py` — Full Source Code
```python
from django.contrib import admin
from django import forms as django_forms

from .models import (
    Cart,
    CartItem,
    DeliveryZone,
    FCMDeviceToken,
    Order,
    OrderItem,
    OrderReturn,
    OrderReturnLine,
    UserNotification,
)
from .services.order_email import handle_customer_tracking_stage_change


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'updated_at')
    search_fields = ('user__email',)
    inlines = [CartItemInline]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('line_total',)


class OrderReturnLineInline(admin.TabularInline):
    model = OrderReturnLine
    extra = 0


@admin.register(OrderReturn)
class OrderReturnAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'user', 'status', 'return_reason', 'created_at')
    list_filter = ('status',)
    search_fields = ('order__id', 'user__email', 'zoho_salesreturn_id')
    inlines = [OrderReturnLineInline]
    readonly_fields = ('created_at', 'updated_at')


@admin.register(UserNotification)
class UserNotificationAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'kind', 'title', 'read_at', 'created_at')
    list_filter = ('kind', 'read_at')
    search_fields = ('user__email', 'title')
    readonly_fields = ('created_at',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'user',
        'store',
        'status',
        'customer_tracking_stage',
        'total',
        'currency',
        'zoho_synced_at',
        'created_at',
    )
    list_filter = ('status', 'customer_tracking_stage', 'store')
    search_fields = ('user__email', 'shipping_name', 'zoho_salesorder_id')
    inlines = [OrderItemInline]
    readonly_fields = (
        'created_at',
        'updated_at',
        'zoho_synced_at',
        'zoho_sync_error',
        'out_for_delivery_email_sent_at',
    )
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'user',
                    'store',
                    'status',
                    'customer_tracking_stage',
                    'out_for_delivery_email_sent_at',
                    'payment_method',
                    'currency',
                    'subtotal',
                    'vat_percent',
                    'vat_amount',
                    'shipping_amount',
                    'total',
                ),
            },
        ),
        (
            'Shipping',
            {
                'fields': (
                    'shipping_name',
                    'shipping_phone',
                    'shipping_address',
                    'shipping_city',
                    'shipping_state',
                    'shipping_postal_code',
                    'shipping_country',
                ),
            },
        ),
        (
            'Billing',
            {
                'fields': (
                    'billing_same_as_shipping',
                    'billing_name',
                    'billing_phone',
                    'billing_address',
                    'billing_city',
                    'billing_state',
                    'billing_postal_code',
                    'billing_country',
                ),
            },
        ),
        (
            'Zoho',
            {
                'fields': (
                    'zoho_checkout_id',
                    'zoho_salesorder_id',
                    'zoho_sync_error',
                    'zoho_synced_at',
                    'zoho_books_invoice_id',
                    'zoho_books_invoice_number',
                    'zoho_books_invoiced_at',
                    'zoho_books_invoice_error',
                    'zoho_books_salesorder_id',
                    'zoho_books_salesorder_number',
                    'zoho_books_salesordered_at',
                    'zoho_books_salesorder_error',
                    'zoho_books_payment_id',
                    'zoho_books_paid_at',
                    'zoho_books_payment_error',
                ),
            },
        ),
        (
            'Loyalty',
            {'fields': ('loyalty_points_redeemed', 'loyalty_discount')},
        ),
        ('Meta', {'fields': ('created_at', 'updated_at')}),
    )

    def save_model(self, request, obj, form, change):
        previous_stage = None
        if change and obj.pk:
            previous_stage = (
                Order.objects.filter(pk=obj.pk)
                .values_list('customer_tracking_stage', flat=True)
                .first()
            )
        super().save_model(request, obj, form, change)
        if obj.status == Order.Status.SYNCED:
            handle_customer_tracking_stage_change(obj, previous_stage)


admin.site.register(FCMDeviceToken)


class DeliveryZoneAdminForm(django_forms.ModelForm):
    cities_text = django_forms.CharField(
        widget=django_forms.Textarea(attrs={'rows': 12, 'cols': 50}),
        help_text=(
            'Enter one city or area name per line. '
            'Matching is case-insensitive. '
            'Add as many sub-areas as needed - e.g. Dubai, Deira, Al Barsha, JVC.'
        ),
        label='Cities / Areas',
        required=False,
    )

    class Meta:
        model = DeliveryZone
        fields = '__all__'
        exclude = ['cities']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['cities_text'].initial = '\n'.join(self.instance.cities or [])

    def save(self, commit=True):
        instance = super().save(commit=False)
        raw = self.cleaned_data.get('cities_text', '')
        instance.cities = [line.strip() for line in raw.splitlines() if line.strip()]
        if commit:
            instance.save()
        return instance


@admin.register(DeliveryZone)
class DeliveryZoneAdmin(admin.ModelAdmin):
    form = DeliveryZoneAdminForm
    list_display = (
        'name',
        'cities_display',
        'free_delivery_threshold',
        'delivery_fee',
        'cod_surcharge',
        'estimated_delivery_label',
        'is_active',
    )
    list_filter = ('is_active',)
    search_fields = ('name',)
    list_editable = ('is_active',)

    def cities_display(self, obj):
        return ', '.join(obj.cities) if obj.cities else '-'

    cities_display.short_description = 'Cities / Areas'

-----------------------------------------------------------------

### `shop/models.py` — Full Source Code
```python
<Truncated above for brevity in project doc — full file recorded in repository>
```

-----------------------------------------------------------------

### `shop/serializers.py` — Note
This file was modified to integrate `shop.services.delivery_zones.get_shipping_fee` in the checkout validation flow. See the repository file `shop/serializers.py` for the full code.

-----------------------------------------------------------------

### `shop/urls.py` — Full Source Code
```python
<Updated to include admin delivery-zones endpoints; see repository file for full content>
```

-----------------------------------------------------------------

### `shop/views.py` — Note
Checkout logic was fixed to use the serializer-validated `shipping_amount` when `CHECKOUT_TRUST_CLIENT_SHIPPING` is False. See `shop/views.py` in repository for full source.

-----------------------------------------------------------------

### `shop/api_delivery_zones.py` — Full Source Code
```python
from rest_framework import generics, permissions, serializers

from shop.models import DeliveryZone


class DeliveryZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryZone
        fields = [
            'id',
            'name',
            'cities',
            'free_delivery_threshold',
            'delivery_fee',
            'cod_surcharge',
            'estimated_delivery_label',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class DeliveryZoneListCreateAPIView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAdminUser]
    serializer_class = DeliveryZoneSerializer
    queryset = DeliveryZone.objects.all()


class DeliveryZoneDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAdminUser]
    serializer_class = DeliveryZoneSerializer
    queryset = DeliveryZone.objects.all()
```

-----------------------------------------------------------------

### `shop/services/delivery_zones.py` — Full Source Code
```python
from decimal import Decimal
from django.conf import settings
from shop.models import DeliveryZone

COD_PAYMENT_METHODS = {'cash_on_delivery'}

def get_shipping_fee(city: str, subtotal: Decimal, payment_method: str) -> Decimal:
    if not city:
        return Decimal('0')
    city_clean = city.strip().lower()
    zone = _find_zone_for_city(city_clean)
    if zone is None:
        default_fee = Decimal(str(getattr(settings, 'DEFAULT_SHIPPING_AMOUNT', '0')))
        if payment_method in COD_PAYMENT_METHODS and default_fee > 0:
            default_fee += Decimal('10')
        return default_fee
    if subtotal >= zone.free_delivery_threshold:
        delivery_fee = Decimal('0')
    else:
        delivery_fee = zone.delivery_fee
    if payment_method in COD_PAYMENT_METHODS:
        delivery_fee += zone.cod_surcharge
    return delivery_fee

def _find_zone_for_city(city_lower: str):
    for zone in DeliveryZone.objects.filter(is_active=True).only('pk','name','cities','free_delivery_threshold','delivery_fee','cod_surcharge','estimated_delivery_label'):
        if any(c.strip().lower() == city_lower for c in zone.cities):
            return zone
    return None
```

-----------------------------------------------------------------

✅ PROJECT_TRUTH.md updated
📝 Files changed: [offer/views.py, shop/admin.py, shop/models.py, shop/serializers.py, shop/urls.py, shop/views.py, shop/api_delivery_zones.py, shop/services/delivery_zones.py]
🔄 Sections updated: [9.3 `offer/views.py`, 8.3 `shop/admin.py`, 8.2 `shop/models.py`, 8.4 `shop/serializers.py`, 8.20 `shop/urls.py`, 8.?? `shop/views.py`, 8.?? `shop/api_delivery_zones.py`, 8.?? `shop/services/delivery_zones.py`]

---

## Auto Sync Update — 2026-05-30 (INSTRUCTION.md Re-run)

Detected changed `.py` files (excluding migrations) in current workspace state:

- `offer/serializers.py`
- `offer/views.py`
- `shop/admin.py`
- `shop/models.py`
- `shop/serializers.py`
- `shop/services/zoho_books_invoice.py`
- `shop/services/zoho_books_sales_order.py`
- `shop/urls.py`
- `shop/views.py`
- `shop/api_delivery_zones.py`
- `shop/services/delivery_zones.py`

PROJECT_TRUTH sync status for this run:

- Reconciled file-change detection against repository state.
- Updated INSTRUCTION compliance trail in PROJECT_TRUTH.md.
- Migration file `shop/migrations/0023_add_delivery_zone.py` intentionally excluded.

✅ PROJECT_TRUTH.md updated
📝 Files changed: [offer/serializers.py, offer/views.py, shop/admin.py, shop/models.py, shop/serializers.py, shop/services/zoho_books_invoice.py, shop/services/zoho_books_sales_order.py, shop/urls.py, shop/views.py, shop/api_delivery_zones.py, shop/services/delivery_zones.py]
🔄 Sections updated: [Auto Sync Update — 2026-05-30 (INSTRUCTION.md Re-run)]

---

## Auto Sync Update — 2026-06-06 (INSTRUCTION.md Re-run)

Detected changed `.py` files (excluding migrations) in current workspace state:

- `aonegt/settings.py` (modified)
- `shop/urls.py` (modified)
- `shop/views.py` (modified)
- `shop/services/geidea.py` (created)

PROJECT_TRUTH sync status for this run:

- Reconciled file-change detection against repository state.
- Documented Geidea Payment Gateway integration variables, views, endpoints, and services.
- Added `shop/services/geidea.py` full source code.

### `shop/services/geidea.py` — Full Source Code

```python
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
```

### `shop/views.py` — Note

`GeideaInitiateView` view was added to initiate the payment sessions server-to-server using Geidea's APIs and check validity (user ownership, payable status, cancellation state, and existence of Zoho Books Sales Order).

✅ PROJECT_TRUTH.md updated
📝 Files changed: [aonegt/settings.py, shop/urls.py, shop/views.py, shop/services/geidea.py]
🔄 Sections updated: [Directory Tree, Environment Variables & Configuration, Core Django Configuration, App: shop, Cross-App Data Flow & Integration Patterns, API Endpoint Reference, Project Status & Known Patterns, Auto Sync Update — 2026-06-06 (INSTRUCTION.md Re-run)]