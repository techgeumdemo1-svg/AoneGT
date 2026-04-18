"""
Helpers for Zoho Commerce sites (shops) index API.

Docs endpoint:
    GET {ZOHO_COMMERCE_BASE_URL}/zs-site/api/v1/index/sites
"""
from __future__ import annotations

from typing import Any, Optional

import requests
from django.conf import settings

from catalog.models import Store
from zoho_integration.models import ZohoCommerceAccount
from shop.services.zoho_commerce import ZohoCommerceError, ZohoCommerceService


def _resolve_account_key(account: Optional[str]) -> str:
    key = str(account or 'primary').strip().lower()
    if key not in ('primary', 'secondary'):
        raise ZohoCommerceError("account must be 'primary' or 'secondary'.")
    return key


def _commerce_base_for_account(account: Optional[str]) -> str:
    key = _resolve_account_key(account)
    if key == 'secondary':
        return (
            getattr(settings, 'ZOHO_SECONDARY_COMMERCE_BASE_URL', '')
            or getattr(settings, 'ZOHO_COMMERCE_BASE_URL', '')
            or 'https://commerce.zoho.com'
        ).rstrip('/')
    return (getattr(settings, 'ZOHO_COMMERCE_BASE_URL', '') or 'https://commerce.zoho.com').rstrip('/')


def _refresh_access_token_for_account(account: Optional[str]) -> str:
    key = _resolve_account_key(account)
    if key == 'primary':
        return ZohoCommerceService.refresh_access_token()

    refresh_token = (getattr(settings, 'ZOHO_SECONDARY_REFRESH_TOKEN', '') or '').strip()
    client_id = (getattr(settings, 'ZOHO_SECONDARY_CLIENT_ID', '') or '').strip()
    client_secret = (getattr(settings, 'ZOHO_SECONDARY_CLIENT_SECRET', '') or '').strip()
    if not (refresh_token and client_id and client_secret):
        raise ZohoCommerceError(
            'Set ZOHO_SECONDARY_REFRESH_TOKEN, ZOHO_SECONDARY_CLIENT_ID, and '
            'ZOHO_SECONDARY_CLIENT_SECRET for secondary account shop listing.',
        )
    access_token, _exp = ZohoCommerceService._refresh_with_creds(
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
    )
    return access_token


def _extract_sites(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    get_sites = payload.get('get_sites')
    if not isinstance(get_sites, dict):
        return []
    my_sites = get_sites.get('my_sites')
    if not isinstance(my_sites, list):
        return []
    return [s for s in my_sites if isinstance(s, dict)]


def _map_shop(site: dict[str, Any]) -> dict[str, Any]:
    return {
        'shop_id': str(site.get('zsite_id') or '').strip(),
        'shop_name': str(site.get('site_title') or '').strip(),
        'domain': str(site.get('primary_domain') or '').strip(),
        'finance_org_id': str(site.get('zohofinance_orgid') or '').strip(),
        'organization_id': str(site.get('zohofinance_orgid') or '').strip(),
        'currency_code': str(site.get('currency_code') or '').strip(),
        'country_code': str(site.get('country_code') or '').strip(),
        'store_enabled': bool(site.get('store_enabled')),
    }


def _fetch_sites_with_token(base: str, token: str) -> Any:
    url = f'{base}/zs-site/api/v1/index/sites'
    headers = {'Authorization': f'Zoho-oauthtoken {token}'}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        status_code = getattr(resp, 'status_code', 'unknown')
        body = (getattr(resp, 'text', '') or '').strip()
        details = body[:500] if body else 'no response body'
        raise ZohoCommerceError(
            f'Zoho sites request failed (HTTP {status_code}): {details}',
        ) from e
    except requests.RequestException as e:
        raise ZohoCommerceError(f'Zoho sites request failed: {e}') from e
    except ValueError as e:
        raise ZohoCommerceError('Invalid JSON from Zoho sites endpoint.') from e


def _refresh_access_token_for_account_model(account: ZohoCommerceAccount) -> str:
    token_url = f"{(account.accounts_url or 'https://accounts.zoho.com').rstrip('/')}/oauth/v2/token"
    payload = {
        'refresh_token': (account.refresh_token or '').strip(),
        'client_id': (account.client_id or '').strip(),
        'client_secret': (account.client_secret or '').strip(),
        'grant_type': 'refresh_token',
    }
    if not (payload['refresh_token'] and payload['client_id'] and payload['client_secret']):
        raise ZohoCommerceError(
            f'Account "{account.name}" is missing refresh_token/client_id/client_secret.',
        )
    try:
        response = requests.post(token_url, data=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        raise ZohoCommerceError(f'Zoho token refresh failed for "{account.name}": {e}') from e
    except ValueError as e:
        raise ZohoCommerceError(f'Invalid JSON from token endpoint for "{account.name}".') from e
    access_token = (data.get('access_token') or '').strip()
    if not access_token:
        raise ZohoCommerceError(f'No access_token returned for "{account.name}".')
    return access_token


def fetch_zoho_shops(*, account: str = 'primary') -> list[dict[str, Any]]:
    """
    Return normalized shop records for mobile UI.
    """
    base = _commerce_base_for_account(account)
    token = _refresh_access_token_for_account(account)
    payload = _fetch_sites_with_token(base, token)
    shops = [_map_shop(site) for site in _extract_sites(payload)]
    return [s for s in shops if s['shop_id']]


def fetch_zoho_shops_from_stores(*, store_id: int | None = None) -> dict[str, Any]:
    """
    Fetch Zoho shops using per-store OAuth credentials saved in catalog.Store.
    Returns summary with shops and per-store errors.
    """
    qs = Store.objects.filter(is_active=True).order_by('sort_order', 'name')
    if store_id is not None:
        qs = qs.filter(pk=store_id)
    stores = list(qs)
    if not stores:
        raise ZohoCommerceError('No active stores found for database-credentials mode.')

    base = (getattr(settings, 'ZOHO_COMMERCE_BASE_URL', '') or 'https://commerce.zoho.com').rstrip('/')
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for store in stores:
        try:
            token = ZohoCommerceService.refresh_access_token(store=store)
            payload = _fetch_sites_with_token(base, token)
            shops = [_map_shop(site) for site in _extract_sites(payload)]
            shops = [s for s in shops if s['shop_id']]
            for shop in shops:
                shop['store_pk'] = store.pk
                shop['store_name'] = store.name
            results.extend(shops)
        except ZohoCommerceError as e:
            errors.append(
                {
                    'store_pk': store.pk,
                    'store_name': store.name,
                    'error': str(e),
                }
            )

    return {
        'shops': results,
        'errors': errors,
        'processed_store_count': len(stores),
    }


def fetch_zoho_shops_from_accounts(*, account_id: int | None = None) -> dict[str, Any]:
    """
    Fetch Zoho shops using zoho_integration.ZohoCommerceAccount records.
    Returns summary with shops and per-account errors.
    """
    qs = ZohoCommerceAccount.objects.filter(is_active=True).order_by('name')
    if account_id is not None:
        qs = qs.filter(pk=account_id)
    accounts = list(qs)
    if not accounts:
        raise ZohoCommerceError('No active ZohoCommerceAccount records found.')

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for account in accounts:
        try:
            token = _refresh_access_token_for_account_model(account)
            base = (account.commerce_base_url or 'https://commerce.zoho.com').rstrip('/')
            payload = _fetch_sites_with_token(base, token)
            shops = [_map_shop(site) for site in _extract_sites(payload)]
            shops = [s for s in shops if s['shop_id']]
            for shop in shops:
                shop['account_id'] = account.pk
                shop['account_name'] = account.name
                shop['account_email'] = account.email
            results.extend(shops)
        except ZohoCommerceError as e:
            errors.append(
                {
                    'account_id': account.pk,
                    'account_name': account.name,
                    'account_email': account.email,
                    'error': str(e),
                }
            )

    return {
        'shops': results,
        'errors': errors,
        'processed_account_count': len(accounts),
    }


def _as_amount(raw: Any) -> str:
    if raw is None:
        return '0'
    text = str(raw).strip()
    return text or '0'


def _extract_products(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ('products', 'data', 'items'):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [p for p in rows if isinstance(p, dict)]
    return []


def _map_product(product: dict[str, Any]) -> dict[str, Any]:
    product_id = str(
        product.get('product_id')
        or product.get('id')
        or product.get('variant_id')
        or '',
    ).strip()
    return {
        'product_id': product_id,
        'name': str(product.get('name') or product.get('product_name') or '').strip(),
        'sku': str(product.get('sku') or '').strip(),
        'price': _as_amount(product.get('rate') or product.get('price') or product.get('min_rate')),
        'sale_price': _as_amount(product.get('sale_price')),
        'stock': _as_amount(product.get('stock_on_hand') or product.get('stock')),
        'image_url': str(product.get('image_url') or product.get('image_name') or '').strip(),
        'image_name': str(product.get('image_name') or '').strip(),
        'image_document_id': str(product.get('image_document_id') or '').strip(),
        'status': str(product.get('status') or '').strip(),
    }


def fetch_zoho_shop_products(
    shop_id: str,
    *,
    page: int = 1,
    per_page: int = 20,
    account: str = 'primary',
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Resolve a shop by id from Zoho sites and fetch storefront products for that shop domain.
    """
    sid = str(shop_id or '').strip()
    if not sid:
        raise ZohoCommerceError('shop_id is required.')

    shops = fetch_zoho_shops(account=account)
    shop = next((s for s in shops if s.get('shop_id') == sid), None)
    if not shop:
        raise ZohoCommerceError('Shop not found in Zoho sites.')
    domain = (shop.get('domain') or '').strip()
    if not domain:
        raise ZohoCommerceError('Selected shop has no primary domain.')

    base = _commerce_base_for_account(account)
    url = f'{base}/storefront/api/v1/products'
    params = {'page': int(page or 1), 'per_page': int(per_page or 20), 'format': 'json'}
    try:
        response = requests.get(url, headers={'domain-name': domain}, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as e:
        raise ZohoCommerceError(f'Zoho storefront products request failed: {e}') from e
    except ValueError as e:
        raise ZohoCommerceError('Invalid JSON from Zoho storefront products.') from e

    products = [_map_product(p) for p in _extract_products(payload)]
    products = [p for p in products if p['product_id']]
    return shop, products
