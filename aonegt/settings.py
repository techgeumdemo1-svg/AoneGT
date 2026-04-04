from pathlib import Path
import os
from datetime import timedelta
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent.parent

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
    'corsheaders',
    'accounts',
    'catalog',
    'shop',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
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

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'aonegt_db'),
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
    }
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

STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'accounts.User'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.AllowAny',
    ),
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'AUTH_HEADER_TYPES': ('Bearer',),
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
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'webmaster@localhost')
FRONTEND_RESET_URL = os.getenv('FRONTEND_RESET_URL', 'aonegt://reset-password')

# --- Checkout: do not trust client shipping by default (set true only for dev / custom quotes). ---
CHECKOUT_TRUST_CLIENT_SHIPPING = os.getenv(
    'CHECKOUT_TRUST_CLIENT_SHIPPING', 'False',
).strip().lower() in ('true', '1', 'yes')
try:
    DEFAULT_SHIPPING_AMOUNT = Decimal(os.getenv('DEFAULT_SHIPPING_AMOUNT', '0'))
except Exception:
    DEFAULT_SHIPPING_AMOUNT = Decimal('0')

# --- Zoho: registration gate ---
REGISTER_REQUIRE_ZOHO_CONTACT = os.getenv(
    'REGISTER_REQUIRE_ZOHO_CONTACT', 'False',
).strip().lower() in ('true', '1', 'yes')
REGISTER_ZOHO_EMAIL_SOURCE = os.getenv(
    'REGISTER_ZOHO_EMAIL_SOURCE', 'inventory',
).strip().lower()
ZOHO_API_BASE_HOST = os.getenv('ZOHO_API_BASE_HOST', 'https://www.zohoapis.com').rstrip('/')
ZOHO_INVENTORY_ORGANIZATION_ID = os.getenv('ZOHO_INVENTORY_ORGANIZATION_ID', '').strip()
ZOHO_COMMERCE_ORGANIZATION_ID = os.getenv('ZOHO_COMMERCE_ORGANIZATION_ID', '').strip()
