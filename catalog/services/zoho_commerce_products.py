"""
Proxy helpers for Zoho Commerce store APIs (same auth as sales orders).

List: GET https://commerce.zoho.com/store/api/v1/products
Detail (edit page): GET .../products/editpage?product_id=

OAuth scope: ZohoCommerce.items.READ
Headers: Authorization Zoho-oauthtoken, X-com-zoho-store-organizationid
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

COMMERCE_PRODUCTS_LIST_URL = 'https://commerce.zoho.com/store/api/v1/products'
COMMERCE_PRODUCT_EDITPAGE_URL = 'https://commerce.zoho.com/store/api/v1/products/editpage'

LIST_QUERY_KEYS = frozenset(
    {'filter_by', 'sort_column', 'sort_order', 'page_start_from', 'per_page'},
)


class ZohoCommerceProductError(Exception):
    """Configuration or transport error before a parsed HTTP response is returned."""


def _resolved_commerce_org_id(store: object | None) -> str:
    if store is not None:
        org = (getattr(store, 'zoho_org_id', '') or '').strip()
        if org:
            return org
    return (os.environ.get('ZOHO_COMMERCE_ORGANIZATION_ID') or '').strip()


def _bearer_token_for_store(store: object | None) -> str:
    if store is not None:
        from django.utils import timezone as dj_tz

        at = (getattr(store, 'access_token', '') or '').strip()
        exp = getattr(store, 'token_expiry', None)
        if at and (exp is None or exp > dj_tz.now()):
            return at
    return (os.environ.get('ZOHO_ACCESS_TOKEN') or '').strip()


def _store_auth_headers(store: object | None = None) -> dict[str, str]:
    token = _bearer_token_for_store(store)
    org_id = _resolved_commerce_org_id(store)
    if not token or not org_id:
        raise ZohoCommerceProductError(
            'Set ZOHO_ACCESS_TOKEN (or Store.access_token) and a Commerce org id '
            'on the Store (zoho_org_id) or ZOHO_COMMERCE_ORGANIZATION_ID in the environment.',
        )
    return {
        'Authorization': f'Zoho-oauthtoken {token}',
        'X-com-zoho-store-organizationid': org_id,
    }


def zoho_commerce_proxy_get(url: str, *, store: object | None = None) -> tuple[int, Any]:
    """
    GET url with Commerce store headers. Returns (http_status, body).
    body is parsed JSON when possible; otherwise a string of the response body.

    Pass ``store`` to use that store's ``zoho_org_id`` as organization header
    and optional ``access_token`` when valid.
    """
    req = Request(url, headers=_store_auth_headers(store), method='GET')
    try:
        with urlopen(req, timeout=60) as resp:
            raw = resp.read().decode('utf-8')
            status = getattr(resp, 'status', 200) or 200
    except HTTPError as e:
        status = e.code
        raw = e.read().decode('utf-8', errors='replace')
    except URLError as e:
        raise ZohoCommerceProductError(f'Could not reach Zoho Commerce: {e}') from e

    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def build_products_list_url(query_params: dict[str, Any]) -> str:
    items = []
    for key in LIST_QUERY_KEYS:
        if key not in query_params:
            continue
        val = query_params.get(key)
        if val is None or val == '':
            continue
        items.append((key, val))
    if not items:
        return COMMERCE_PRODUCTS_LIST_URL
    return f'{COMMERCE_PRODUCTS_LIST_URL}?{urlencode(items, doseq=True)}'


def build_product_editpage_url(product_id: str) -> str:
    product_id = (product_id or '').strip()
    if not product_id:
        raise ZohoCommerceProductError('product_id is required.')
    return f'{COMMERCE_PRODUCT_EDITPAGE_URL}?{urlencode({"product_id": product_id})}'
