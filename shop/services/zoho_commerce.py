"""
Zoho Commerce **store** API helpers for shop flows (sales orders, checkout sync, etc.).

Static token (urllib helpers):
  - ZOHO_ACCESS_TOKEN
  - ZOHO_COMMERCE_ORGANIZATION_ID (header X-com-zoho-store-organizationid)

:class:`ZohoCommerceService` uses refresh-token OAuth + storefront ``domain-name`` header.
Django settings: ZOHO_ACCOUNTS_URL, ZOHO_CLIENT_*, ZOHO_REFRESH_TOKEN, ZOHO_STORE_DOMAIN,
ZOHO_ORG_ID (falls back to ZOHO_COMMERCE_ORGANIZATION_ID), ZOHO_COMMERCE_BASE_URL.

Paths for urllib_helpers are under ``/store/api/v1/`` — e.g. ``"salesorders"`` or ``"checkouts"``.
"""
from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import requests
from django.conf import settings
from django.utils import timezone as django_timezone

from catalog.models import Store

STORE_API_PREFIX = '/store/api/v1'


class ZohoCommerceError(Exception):
    """Missing configuration or network failure before a response is available."""


def commerce_base_url() -> str:
    return (os.environ.get('ZOHO_COMMERCE_BASE_URL') or 'https://commerce.zoho.com').rstrip('/')


def commerce_store_api_configured() -> bool:
    token = (os.environ.get('ZOHO_ACCESS_TOKEN') or '').strip()
    org = (os.environ.get('ZOHO_COMMERCE_ORGANIZATION_ID') or '').strip()
    return bool(token and org)


def _auth_headers(
    *,
    extra: dict[str, str] | None = None,
    content_type: str | None = None,
) -> dict[str, str]:
    token = (os.environ.get('ZOHO_ACCESS_TOKEN') or '').strip()
    org_id = (os.environ.get('ZOHO_COMMERCE_ORGANIZATION_ID') or '').strip()
    if not token or not org_id:
        raise ZohoCommerceError(
            'Set ZOHO_ACCESS_TOKEN and ZOHO_COMMERCE_ORGANIZATION_ID for Zoho Commerce.',
        )
    h: dict[str, str] = {
        'Authorization': f'Zoho-oauthtoken {token}',
        'X-com-zoho-store-organizationid': org_id,
    }
    if content_type:
        h['Content-Type'] = content_type
    if extra:
        h.update(extra)
    return h


def commerce_store_url(resource: str, query: dict[str, Any] | None = None) -> str:
    resource = (resource or '').strip().lstrip('/')
    base = f'{commerce_base_url()}{STORE_API_PREFIX}/{resource}'
    if query:
        items = [(k, v) for k, v in query.items() if v is not None and v != '']
        if items:
            return f'{base}?{urlencode(items, doseq=True)}'
    return base


def commerce_store_request(
    method: str,
    resource: str,
    *,
    query: dict[str, Any] | None = None,
    json_data: dict[str, Any] | list[Any] | None = None,
    timeout: int = 60,
) -> tuple[int, Any]:
    """
    Call Zoho Commerce store API. Returns (http_status, body) where body is parsed JSON
    when possible, otherwise the raw response string.
    """
    url = commerce_store_url(resource, query=query)
    m = (method or 'GET').upper()
    data_bytes: bytes | None = None
    headers: dict[str, str]
    if m in ('POST', 'PUT', 'PATCH') and json_data is not None:
        data_bytes = json.dumps(json_data).encode('utf-8')
        headers = _auth_headers(content_type='application/json;charset=UTF-8')
    else:
        headers = _auth_headers()
    req = Request(url, data=data_bytes, headers=headers, method=m)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8')
            status = getattr(resp, 'status', 200) or 200
    except HTTPError as e:
        status = e.code
        raw = e.read().decode('utf-8', errors='replace')
    except URLError as e:
        raise ZohoCommerceError(f'Could not reach Zoho Commerce: {e}') from e

    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def commerce_store_get(
    resource: str,
    *,
    query: dict[str, Any] | None = None,
    timeout: int = 60,
) -> tuple[int, Any]:
    return commerce_store_request('GET', resource, query=query, timeout=timeout)


def commerce_store_post(
    resource: str,
    json_data: dict[str, Any] | list[Any],
    *,
    query: dict[str, Any] | None = None,
    timeout: int = 60,
) -> tuple[int, Any]:
    return commerce_store_request(
        'POST', resource, query=query, json_data=json_data, timeout=timeout,
    )


class ZohoCommerceService:
    """OAuth refresh + Commerce admin headers + storefront product APIs (requests)."""

    @staticmethod
    def _refresh_with_creds(
        *,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> tuple[str, int | None]:
        url = f'{settings.ZOHO_ACCOUNTS_URL}/oauth/v2/token'
        params = {
            'refresh_token': refresh_token,
            'client_id': client_id,
            'client_secret': client_secret,
            'grant_type': 'refresh_token',
        }
        try:
            response = requests.post(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            raise ZohoCommerceError(f'Zoho token refresh request failed: {e}') from e
        except ValueError as e:
            raise ZohoCommerceError('Invalid JSON from Zoho token endpoint.') from e

        access_token = data.get('access_token')
        if not access_token:
            raise ZohoCommerceError(f'Failed to refresh Zoho token: {data}')
        expires_in = data.get('expires_in')
        try:
            exp_secs = int(expires_in) if expires_in is not None else None
        except (TypeError, ValueError):
            exp_secs = None
        return access_token, exp_secs

    @classmethod
    def refresh_access_token(cls, store: Store | None = None) -> str:
        if store is not None:
            rt = (store.refresh_token or '').strip()
            cid = (store.client_id or '').strip()
            cs = (store.client_secret or '').strip()
            if rt and cid and cs:
                access_token, exp_secs = cls._refresh_with_creds(
                    refresh_token=rt,
                    client_id=cid,
                    client_secret=cs,
                )
                if exp_secs is not None:
                    store.token_expiry = django_timezone.now() + timedelta(seconds=exp_secs)
                else:
                    store.token_expiry = None
                store.access_token = access_token
                store.save(update_fields=['access_token', 'token_expiry'])
                return access_token

        if not (
            getattr(settings, 'ZOHO_REFRESH_TOKEN', '')
            and getattr(settings, 'ZOHO_CLIENT_ID', '')
            and getattr(settings, 'ZOHO_CLIENT_SECRET', '')
        ):
            raise ZohoCommerceError(
                'Set ZOHO_REFRESH_TOKEN, ZOHO_CLIENT_ID, and ZOHO_CLIENT_SECRET for token refresh, '
                'or set per-store refresh_token, client_id, and client_secret on the Store.',
            )
        access_token, _exp = cls._refresh_with_creds(
            refresh_token=settings.ZOHO_REFRESH_TOKEN,
            client_id=settings.ZOHO_CLIENT_ID,
            client_secret=settings.ZOHO_CLIENT_SECRET,
        )
        return access_token

    @classmethod
    def admin_headers(cls, store: Store | None = None) -> dict[str, str]:
        org = ''
        if store is not None:
            org = (getattr(store, 'zoho_org_id', '') or '').strip()
        if not org:
            org = (getattr(settings, 'ZOHO_ORG_ID', '') or '').strip()
        if not org:
            raise ZohoCommerceError(
                'Set zoho_org_id on the Store, or ZOHO_ORG_ID / '
                'ZOHO_COMMERCE_ORGANIZATION_ID for admin API requests.',
            )
        access_token = cls.refresh_access_token(store)
        return {
            'Authorization': f'Zoho-oauthtoken {access_token}',
            'X-com-zoho-store-organizationid': org,
        }

    @staticmethod
    def storefront_headers(store: Store | None = None) -> dict[str, str]:
        domain = ''
        if store is not None:
            domain = (getattr(store, 'zoho_store_domain', '') or '').strip()
        if not domain:
            domain = (getattr(settings, 'ZOHO_STORE_DOMAIN', '') or '').strip()
        if not domain:
            raise ZohoCommerceError(
                'Set zoho_store_domain on the Store, or ZOHO_STORE_DOMAIN in settings '
                '(e.g. yourstore.zohostore.com) for storefront API.',
            )
        return {'domain-name': domain}

    @classmethod
    def get_products_storefront(
        cls,
        product_type: str | None = None,
        page: int = 1,
        per_page: int = 20,
        *,
        store: Store | None = None,
    ) -> Any:
        url = f'{settings.ZOHO_COMMERCE_BASE_URL}/storefront/api/v1/products'
        params: dict[str, Any] = {
            'page': page,
            'per_page': per_page,
            'format': 'json',
        }
        if product_type:
            params['product_type'] = product_type
        try:
            response = requests.get(
                url,
                headers=cls.storefront_headers(store),
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise ZohoCommerceError(f'Zoho storefront products request failed: {e}') from e
        except ValueError as e:
            raise ZohoCommerceError('Invalid JSON from Zoho storefront.') from e

    @classmethod
    def get_product_detail_storefront(
        cls, product_id: str, *, store: Store | None = None,
    ) -> Any:
        pid = (product_id or '').strip()
        if not pid:
            raise ZohoCommerceError('product_id is required.')
        url = f'{settings.ZOHO_COMMERCE_BASE_URL}/storefront/api/v1/products/{pid}'
        try:
            response = requests.get(
                url,
                headers=cls.storefront_headers(store),
                params={'format': 'json'},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise ZohoCommerceError(f'Zoho storefront product detail failed: {e}') from e
        except ValueError as e:
            raise ZohoCommerceError('Invalid JSON from Zoho storefront.') from e


__all__ = [
    'ZohoCommerceError',
    'ZohoCommerceService',
    'commerce_base_url',
    'commerce_store_api_configured',
    'commerce_store_get',
    'commerce_store_post',
    'commerce_store_request',
    'commerce_store_url',
]
