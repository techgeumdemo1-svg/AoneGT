"""
Verify that an email exists as a Zoho Inventory contact (customer).

Uses List contacts with the `email` query parameter.
OAuth scope: ZohoInventory.contacts.READ

Docs: https://www.zoho.com/inventory/api/v1/contacts/#list-contacts
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ZohoContactCheckError(Exception):
    """Zoho API or configuration error while checking contact email."""


def zoho_contact_check_configured() -> bool:
    token = (os.environ.get('ZOHO_ACCESS_TOKEN') or '').strip()
    org = (os.environ.get('ZOHO_INVENTORY_ORGANIZATION_ID') or '').strip()
    return bool(token and org)


def _api_base() -> str:
    return (os.environ.get('ZOHO_API_BASE_HOST') or 'https://www.zohoapis.com').rstrip('/')


def inventory_contact_email_exists(email: str) -> bool:
    """
    Return True if a Zoho Inventory contact matches this email (exact, case-insensitive).

    Raises ZohoContactCheckError on HTTP/parse/configuration errors.
    """
    email = (email or '').strip().lower()
    if not email:
        raise ZohoContactCheckError('Email is required.')

    token = (os.environ.get('ZOHO_ACCESS_TOKEN') or '').strip()
    org_id = (os.environ.get('ZOHO_INVENTORY_ORGANIZATION_ID') or '').strip()
    if not token or not org_id:
        raise ZohoContactCheckError(
            'Missing ZOHO_ACCESS_TOKEN or ZOHO_INVENTORY_ORGANIZATION_ID.',
        )

    qs = urlencode(
        {
            'organization_id': org_id,
            'email': email,
            'per_page': 200,
        },
    )
    url = f'{_api_base()}/inventory/v1/contacts?{qs}'
    req = Request(
        url,
        headers={'Authorization': f'Zoho-oauthtoken {token}'},
        method='GET',
    )
    try:
        with urlopen(req, timeout=45) as resp:
            body = resp.read().decode('utf-8')
    except HTTPError as e:
        err = e.read().decode('utf-8', errors='replace')
        raise ZohoContactCheckError(f'Zoho Inventory HTTP {e.code}: {err}') from e
    except URLError as e:
        raise ZohoContactCheckError(f'Could not reach Zoho: {e}') from e

    try:
        data: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as e:
        raise ZohoContactCheckError('Invalid JSON from Zoho Inventory.') from e

    if data.get('code') != 0:
        msg = data.get('message') or data
        raise ZohoContactCheckError(f'Zoho Inventory error: {msg}')

    for c in data.get('contacts') or []:
        if _contact_matches_email(c, email):
            return True
        for p in c.get('contact_persons') or []:
            if (p.get('email') or '').strip().lower() == email:
                return True
    return False


def _contact_matches_email(contact: dict, email_lower: str) -> bool:
    main = (contact.get('email') or '').strip().lower()
    if main == email_lower:
        return True
    return False
