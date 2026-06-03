from pathlib import Path
import os
from datetime import timedelta
from decimal import Decimal
import dj_database_url
from dotenv import load_dotenv

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent.parent

SUPERUSER_API_SECRET = os.environ.get("SUPERUSER_API_SECRET")
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY') or 'change-me-set-DJANGO_SECRET_KEY-in-env'
DEBUG = os.getenv('DEBUG', 'True') == 'True'
ALLOWED_HOSTS = [
    h.strip() for h in os.getenv('ALLOWED_HOSTS', '127.0.0.1,localhost').split(',') if h.strip()
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'accounts',
    'catalog',
    'shop',
    'zoho_integration',
    'offer',
    # 'offers',
    'superuser',
    'admin_dashboard',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'aonegt.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'aonegt.wsgi.application'
ASGI_APPLICATION = 'aonegt.asgi.application'

# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.postgresql',
#         'NAME': os.getenv('DB_NAME', 'aonegt_db'),
#         'USER': os.getenv('DB_USER', 'postgres'),
#         'PASSWORD': os.getenv('DB_PASSWORD', ''),
#         'HOST': os.getenv('DB_HOST', 'localhost'),
#         'PORT': os.getenv('DB_PORT', '5432'),
#     }
# }
DATABASES = {
    'default': dj_database_url.config(
        default=(
            "postgresql://"
            + os.getenv('DB_USER', 'postgres') + ":"
            + os.getenv('DB_PASSWORD', '') + "@"
            + os.getenv('DB_HOST', 'localhost') + ":"
            + os.getenv('DB_PORT', '5432') + "/"
            + os.getenv('DB_NAME', 'aonegt_db')
        ),
        conn_max_age=600,
        ssl_require=not DEBUG,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Dubai'
USE_I18N = True
USE_TZ = True

# STATIC_URL = 'static/'
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'accounts.User'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.AllowAny',
    ),
    'DEFAULT_THROTTLE_RATES': {
        'forgot_password': os.getenv('FORGOT_PASSWORD_THROTTLE_RATE', '5/hour'),
        'deactivate_account_otp': os.getenv('DEACTIVATE_ACCOUNT_OTP_THROTTLE_RATE', '5/hour'),
        'delete_account_otp': os.getenv('DELETE_ACCOUNT_OTP_THROTTLE_RATE', '5/hour'),
        'reactivate_account_otp': os.getenv('REACTIVATE_ACCOUNT_OTP_THROTTLE_RATE', '5/hour'),
        'change_password_otp': os.getenv('CHANGE_PASSWORD_OTP_THROTTLE_RATE', '5/hour'),
        'admin_login_otp': os.getenv('ADMIN_LOGIN_OTP_THROTTLE_RATE', '10/hour'),
    },
}

if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True
else:
    CORS_ALLOW_ALL_ORIGINS = False
    _cors_origins = os.getenv('CORS_ALLOWED_ORIGINS', '').strip()
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors_origins.split(',') if o.strip()]

EMAIL_BACKEND = os.getenv(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend',
)
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True') == 'True'
# Avoid hanging workers for minutes when SMTP is unreachable (common on Render if misconfigured).
try:
    EMAIL_TIMEOUT = int(os.getenv('EMAIL_TIMEOUT', '20'))
except ValueError:
    EMAIL_TIMEOUT = 20
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'webmaster@localhost')
ORDER_CONFIRMATION_EMAIL = os.getenv('ORDER_CONFIRMATION_EMAIL', 'True') == 'True'
ORDER_OUT_FOR_DELIVERY_EMAIL = os.getenv('ORDER_OUT_FOR_DELIVERY_EMAIL', 'True') == 'True'
FRONTEND_RESET_URL = os.getenv('FRONTEND_RESET_URL', 'aonegt://reset-password')

# --- OTP cleanup (background scheduler + manage.py purge_expired_otps) ---
OTP_PURGE_SCHEDULER_ENABLED = os.getenv('OTP_PURGE_SCHEDULER_ENABLED', 'True').strip().lower() in (
    'true',
    '1',
    'yes',
)
try:
    OTP_PURGE_INTERVAL_MINUTES = max(1, int(os.getenv('OTP_PURGE_INTERVAL_MINUTES', '60')))
except ValueError:
    OTP_PURGE_INTERVAL_MINUTES = 60
OTP_PURGE_INCLUDE_USED = os.getenv('OTP_PURGE_INCLUDE_USED', 'False').strip().lower() in (
    'true',
    '1',
    'yes',
)
OTP_PURGE_RUN_ON_START = os.getenv('OTP_PURGE_RUN_ON_START', 'True').strip().lower() in (
    'true',
    '1',
    'yes',
)

# --- Loyalty (AED): earn 1 point per LOYALTY_AED_PER_POINT_EARNED spent; 1 point = LOYALTY_POINT_VALUE_AED off.
LOYALTY_AED_PER_POINT_EARNED = int(os.getenv('LOYALTY_AED_PER_POINT_EARNED', '100'))
LOYALTY_POINT_VALUE_AED = Decimal(os.getenv('LOYALTY_POINT_VALUE_AED', '1'))
LOYALTY_MIN_POINTS_TO_REDEEM = int(os.getenv('LOYALTY_MIN_POINTS_TO_REDEEM', '100'))
LOYALTY_COUPON_EXPIRY_DAYS = int(os.getenv('LOYALTY_COUPON_EXPIRY_DAYS', '90'))

# --- Checkout: do not trust client shipping by default (set true only for dev / custom quotes). ---
CHECKOUT_TRUST_CLIENT_SHIPPING = os.getenv(
    'CHECKOUT_TRUST_CLIENT_SHIPPING', 'False',
).strip().lower() in ('true', '1', 'yes')
# When True, prepaid checkout requires payment_success + gateway_reference (production).
CHECKOUT_REQUIRE_PREPAID_PAYMENT_SUCCESS = os.getenv(
    'CHECKOUT_REQUIRE_PREPAID_PAYMENT_SUCCESS', 'True',
).strip().lower() in ('true', '1', 'yes')
try:
    DEFAULT_SHIPPING_AMOUNT = Decimal(os.getenv('DEFAULT_SHIPPING_AMOUNT', '0'))
except Exception:
    DEFAULT_SHIPPING_AMOUNT = Decimal('0')

# --- Zoho: registration gate ---
REGISTER_REQUIRE_ZOHO_CONTACT = os.getenv(
    'REGISTER_REQUIRE_ZOHO_CONTACT', 'False',
).strip().lower() in ('true', '1', 'yes')
# inventory = Zoho Inventory contacts. commerce_salesorders | commerce | zoho_commerce = Zoho Commerce sales orders by email.
REGISTER_ZOHO_EMAIL_SOURCE = os.getenv(
    'REGISTER_ZOHO_EMAIL_SOURCE', 'inventory',
).strip().lower()
ZOHO_API_BASE_HOST = os.getenv('ZOHO_API_BASE_HOST', 'https://www.zohoapis.com').rstrip('/')
ZOHO_INVENTORY_ORGANIZATION_ID = os.getenv('ZOHO_INVENTORY_ORGANIZATION_ID', '').strip()
ZOHO_COMMERCE_ORGANIZATION_ID = os.getenv('ZOHO_COMMERCE_ORGANIZATION_ID', '').strip()

# When True, register requires POST /api/auth/request-registration-code/ first, then registration_otp on register.
REGISTER_REQUIRE_EMAIL_OTP = os.getenv(
    'REGISTER_REQUIRE_EMAIL_OTP', 'False',
).strip().lower() in ('true', '1', 'yes')

# --- Zoho Commerce: OAuth refresh + storefront (see shop.services.zoho_commerce.ZohoCommerceService) ---
ZOHO_COMMERCE_BASE_URL = os.getenv('ZOHO_COMMERCE_BASE_URL', 'https://commerce.zoho.com').rstrip('/')
ZOHO_ACCOUNTS_URL = os.getenv('ZOHO_ACCOUNTS_URL', 'https://accounts.zoho.com').rstrip('/')
ZOHO_STORE_DOMAIN = os.getenv('ZOHO_STORE_DOMAIN', '').strip()
ZOHO_ORG_ID = (os.getenv('ZOHO_ORG_ID') or ZOHO_COMMERCE_ORGANIZATION_ID or '').strip()
ZOHO_CLIENT_ID = os.getenv('ZOHO_CLIENT_ID', '').strip()
ZOHO_CLIENT_SECRET = os.getenv('ZOHO_CLIENT_SECRET', '').strip()
ZOHO_REFRESH_TOKEN = os.getenv('ZOHO_REFRESH_TOKEN', '').strip()
ZOHO_ACCESS_TOKEN = os.getenv('ZOHO_ACCESS_TOKEN', '').strip()
# Push local orders to Zoho Commerce sales orders at checkout / order edit (shop.services.zoho_sales_order).
ZOHO_COMMERCE_CREATE_SALES_ORDER_ENABLED = os.getenv(
    'ZOHO_COMMERCE_CREATE_SALES_ORDER_ENABLED', 'False',
).strip().lower() in ('true', '1', 'yes')

# Zoho Books invoices (per-store zoho_books_org_id on catalog.Store; see shop.services.zoho_books)
ZOHO_BOOKS_CREATE_INVOICE_ENABLED = os.getenv(
    'ZOHO_BOOKS_CREATE_INVOICE_ENABLED', 'False',
).strip().lower() in ('true', '1', 'yes')
ZOHO_BOOKS_ORGANIZATION_ID = os.getenv('ZOHO_BOOKS_ORGANIZATION_ID', '').strip()
ZOHO_BOOKS_CREATE_INVOICE_ON = os.getenv('ZOHO_BOOKS_CREATE_INVOICE_ON', 'synced').strip().lower()
ZOHO_BOOKS_CREATE_SALES_ORDER_ENABLED = os.getenv(
    'ZOHO_BOOKS_CREATE_SALES_ORDER_ENABLED', 'False',
).strip().lower() in ('true', '1', 'yes')
ZOHO_BOOKS_CREATE_SALES_ORDER_ON = os.getenv('ZOHO_BOOKS_CREATE_SALES_ORDER_ON', 'placed').strip().lower()
ZOHO_BOOKS_MANUAL_WORKFLOW = os.getenv(
    'ZOHO_BOOKS_MANUAL_WORKFLOW', 'True',
).strip().lower() in ('true', '1', 'yes')
ZOHO_BOOKS_INVOICE_FROM_SALES_ORDER = os.getenv(
    'ZOHO_BOOKS_INVOICE_FROM_SALES_ORDER', 'True',
).strip().lower() in ('true', '1', 'yes')
ZOHO_BOOKS_VAT_TAX_ID = os.getenv('ZOHO_BOOKS_VAT_TAX_ID', '').strip()
# Days until due date for cash/card on delivery invoices (Zoho default without this is due same day).
ZOHO_BOOKS_PAY_ON_DELIVERY_DUE_DAYS = int(os.getenv('ZOHO_BOOKS_PAY_ON_DELIVERY_DUE_DAYS', '7') or '7')

# Optional image fallback used by /api/shop/zoho-products/<id>/image/ when Zoho has no image URL.
ZOHO_IMAGE_PLACEHOLDER_URL = os.getenv(
    'ZOHO_IMAGE_PLACEHOLDER_URL',
    'https://placehold.co/600x600?text=No+Image',
).strip()
# Comma-separated Zoho storefront collection ids to probe when saving a product
# (Storefront Get Collection API). See zoho_integration.storefront_collections.
ZOHO_COLLECTION_PROBE_IDS = os.getenv('ZOHO_COLLECTION_PROBE_IDS', '').strip()
# GET /zoho/multi/best-deals/ source: admin | category | collection
# Set SOURCE=collection + COLLECTION_ID to drive best deals from a Zoho storefront collection.
ZOHO_BEST_DEALS_COLLECTION_ID = os.getenv('ZOHO_BEST_DEALS_COLLECTION_ID', '').strip()
ZOHO_BEST_DEALS_COLLECTION_NAME = os.getenv('ZOHO_BEST_DEALS_COLLECTION_NAME', 'Best Deals').strip()
_best_deals_source_env = os.getenv('ZOHO_BEST_DEALS_SOURCE', '').strip().lower()
if _best_deals_source_env:
    ZOHO_BEST_DEALS_SOURCE = _best_deals_source_env
elif ZOHO_BEST_DEALS_COLLECTION_ID:
    ZOHO_BEST_DEALS_SOURCE = 'collection'
else:
    ZOHO_BEST_DEALS_SOURCE = 'admin'
ZOHO_BEST_DEALS_CATEGORY_ID = os.getenv('ZOHO_BEST_DEALS_CATEGORY_ID', '').strip()
ZOHO_BEST_DEALS_CATEGORY_NAME = os.getenv('ZOHO_BEST_DEALS_CATEGORY_NAME', 'Best Deals').strip()


ZOHO_REDIRECT_URI = os.getenv("ZOHO_REDIRECT_URI")

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=5),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=5),
    'AUTH_HEADER_TYPES': ('Bearer',),
}


MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ── Production Security Settings ────────────────────────────────────────────
# These only activate when DEBUG=False (i.e., on Render, not on your computer)
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True


import json
import logging

_logger = logging.getLogger(__name__)

try:
    import firebase_admin
    from firebase_admin import credentials as fb_credentials
except ImportError:
    firebase_admin = None  # type: ignore
    fb_credentials = None  # type: ignore
    _logger.warning(
        'firebase-admin not installed — push notifications disabled. '
        'Install with: pip install firebase-admin',
    )

if firebase_admin is not None and fb_credentials is not None and not firebase_admin._apps:
    _fb_cred_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', '')
    _fb_cred_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON', '')

    if _fb_cred_path and os.path.isfile(_fb_cred_path):
        cred = fb_credentials.Certificate(_fb_cred_path)
        firebase_admin.initialize_app(cred)
    elif _fb_cred_json:
        try:
            cred_dict = json.loads(_fb_cred_json)
            cred = fb_credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
        except Exception as exc:
            _logger.warning(
                'Firebase JSON parse failed — push notifications disabled. Error: %s',
                exc,
            )
    else:
        _logger.warning(
            'Firebase credentials not found — push notifications disabled.',
        )