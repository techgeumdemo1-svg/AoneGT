"""Zoho Commerce admin API: list collections by organization (zohoapis.com/commerce/v1)."""

from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings

from catalog.models import Store
from shop.services.zoho_commerce import ZohoCommerceError, ZohoCommerceService

logger = logging.getLogger(__name__)


def zoho_api_base_host() -> str:
    return (getattr(settings, 'ZOHO_API_BASE_HOST', '') or 'https://www.zohoapis.com').rstrip('/')


def _collection_rows_from_payload(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if not isinstance(data, dict):
        return []
    for key in ('collections', 'collection', 'data', 'items'):
        rows = data.get(key)
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
        if isinstance(rows, dict):
            return [rows]
    return []


def collection_summary(row: dict) -> dict:
    return {
        'collection_id': str(row.get('collection_id') or row.get('id') or '').strip(),
        'name': str(row.get('name') or row.get('collection_name') or '').strip(),
        'url': str(row.get('url') or row.get('collection_url') or '').strip(),
        'status': str(row.get('status') or '').strip(),
    }


def list_zoho_commerce_collections(
    organization_id: str,
    *,
    store: Store | None = None,
    timeout: int = 30,
) -> list[dict]:
    """
    GET {ZOHO_API_BASE_HOST}/commerce/v1/collections?organization_id=...
    Requires OAuth (ZohoCommerceService.admin_headers).
    """
    org = str(organization_id or '').strip()
    if not org:
        raise ZohoCommerceError('organization_id is required to list collections.')

    url = f'{zoho_api_base_host()}/commerce/v1/collections'
    headers = ZohoCommerceService.admin_headers(store)
    try:
        response = requests.get(
            url,
            headers=headers,
            params={'organization_id': org},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise ZohoCommerceError(f'Could not reach Zoho Commerce collections API: {exc}') from exc

    if response.status_code >= 400:
        body = (response.text or '')[:500]
        raise ZohoCommerceError(
            f'Zoho collections API returned HTTP {response.status_code}: {body}',
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise ZohoCommerceError('Invalid JSON from Zoho collections API.') from exc

    return _collection_rows_from_payload(data)


def resolve_collection_id_by_name(
    rows: list[dict],
    collection_name: str,
) -> tuple[str, str]:
    """Return (collection_id, resolved_name) or ('', '') if not found."""
    want = (collection_name or '').strip().lower()
    if not want:
        return '', ''
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get('name') or row.get('collection_name') or '').strip()
        if name.lower() != want:
            continue
        cid = str(row.get('collection_id') or row.get('id') or '').strip()
        if cid:
            return cid, name
    return '', ''
