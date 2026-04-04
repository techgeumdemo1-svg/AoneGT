"""
Check if an email appears on Zoho Commerce sales orders (search node `email`).

OAuth scope: ZohoCommerce.salesorders.READ
Header: X-com-zoho-store-organizationid (Commerce org id)

Docs: https://www.zoho.com/commerce/api/list-all-sales-orders.html

Limitation: this is true only if the email has at least one sales order in Commerce.
A customer in the Commerce UI with no orders yet may not match.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .zoho_inventory_contact import ZohoContactCheckError

COMMERCE_SALESORDERS_URL = 'https://commerce.zoho.com/store/api/v1/salesorders'


def zoho_commerce_check_configured() -> bool:
    token = (os.environ.get('ZOHO_ACCESS_TOKEN') or '').strip()
    org = (os.environ.get('ZOHO_COMMERCE_ORGANIZATION_ID') or '').strip()
    return bool(token and org)


def commerce_salesorders_email_exists(email: str) -> bool:
    email = (email or '').strip().lower()
    if not email:
        raise ZohoContactCheckError('Email is required.')

    token = (os.environ.get('ZOHO_ACCESS_TOKEN') or '').strip()
    org_id = (os.environ.get('ZOHO_COMMERCE_ORGANIZATION_ID') or '').strip()
    if not token or not org_id:
        raise ZohoContactCheckError(
            'Missing ZOHO_ACCESS_TOKEN or ZOHO_COMMERCE_ORGANIZATION_ID.',
        )

    qs = urlencode(
        {
            'email': email,
            'per_page': 50,
            'page': 1,
        },
    )
    url = f'{COMMERCE_SALESORDERS_URL}?{qs}'
    req = Request(
        url,
        headers={
            'Authorization': f'Zoho-oauthtoken {token}',
            'X-com-zoho-store-organizationid': org_id,
        },
        method='GET',
    )
    try:
        with urlopen(req, timeout=45) as resp:
            body = resp.read().decode('utf-8')
    except HTTPError as e:
        err = e.read().decode('utf-8', errors='replace')
        raise ZohoContactCheckError(f'Zoho Commerce HTTP {e.code}: {err}') from e
    except URLError as e:
        raise ZohoContactCheckError(f'Could not reach Zoho Commerce: {e}') from e

    try:
        data: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as e:
        raise ZohoContactCheckError('Invalid JSON from Zoho Commerce.') from e

    if data.get('code') != 0:
        msg = data.get('message') or data
        raise ZohoContactCheckError(f'Zoho Commerce error: {msg}')

    for so in data.get('salesorders') or []:
        em = (so.get('email') or '').strip().lower()
        if em == email:
            return True
    return False
