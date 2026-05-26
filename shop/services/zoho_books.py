"""Zoho Books API v3 client (contacts + invoices)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import requests
from django.conf import settings

from shop.services.zoho_commerce import ZohoCommerceError, ZohoCommerceService

logger = logging.getLogger(__name__)


class ZohoBooksError(Exception):
    """Configuration, auth, or Books API failure."""


def zoho_books_enabled() -> bool:
    return getattr(settings, 'ZOHO_BOOKS_CREATE_INVOICE_ENABLED', False)


def zoho_books_organization_id(*, store=None) -> str:
    """
    Resolve Books org id: per-store ``zoho_books_org_id`` first, then global env fallback.
    Commerce ``zoho_org_id`` is not used (Books and Commerce org ids differ).
    """
    if store is not None:
        per_store = (getattr(store, 'zoho_books_org_id', '') or '').strip()
        if per_store:
            return per_store
    return (getattr(settings, 'ZOHO_BOOKS_ORGANIZATION_ID', '') or '').strip()


def zoho_books_vat_tax_id(*, store=None) -> str:
    if store is not None:
        per_store = (getattr(store, 'zoho_books_vat_tax_id', '') or '').strip()
        if per_store:
            return per_store
    return (getattr(settings, 'ZOHO_BOOKS_VAT_TAX_ID', '') or '').strip()


def store_has_books_config(store) -> bool:
    return bool(zoho_books_organization_id(store=store))


def _books_base_url() -> str:
    host = (getattr(settings, 'ZOHO_API_BASE_HOST', 'https://www.zohoapis.com') or '').rstrip('/')
    return f'{host}/books/v3'


def _access_token(*, store=None) -> str:
    token = (getattr(settings, 'ZOHO_ACCESS_TOKEN', '') or '').strip()
    if token:
        return token
    # Per-store cached tokens are often Commerce-only; Books scopes live on .env OAuth.
    global_refresh = (getattr(settings, 'ZOHO_REFRESH_TOKEN', '') or '').strip()
    if global_refresh:
        try:
            return ZohoCommerceService.refresh_access_token(None)
        except ZohoCommerceError:
            pass
    try:
        return ZohoCommerceService.refresh_access_token(store)
    except ZohoCommerceError as exc:
        raise ZohoBooksError(str(exc)) from exc


def _books_request(
    method: str,
    resource: str,
    *,
    store=None,
    query: dict[str, Any] | None = None,
    json_data: dict[str, Any] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    org_id = zoho_books_organization_id(store=store)
    if not org_id:
        store_name = getattr(store, 'name', None) or 'this store'
        raise ZohoBooksError(
            f'Set zoho_books_org_id on Store "{store_name}" or ZOHO_BOOKS_ORGANIZATION_ID in .env.',
        )

    q = dict(query or {})
    q.setdefault('organization_id', org_id)
    resource = (resource or '').strip().lstrip('/')
    url = f'{_books_base_url()}/{resource}?{urlencode(q)}'

    headers = {
        'Authorization': f'Zoho-oauthtoken {_access_token(store=store)}',
    }
    if json_data is not None:
        headers['Content-Type'] = 'application/json'

    try:
        response = requests.request(
            method.upper(),
            url,
            headers=headers,
            json=json_data,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise ZohoBooksError(f'Zoho Books request failed: {exc}') from exc

    try:
        body = response.json()
    except ValueError:
        body = {'raw': response.text[:2000]}

    if response.status_code >= 400:
        message = body.get('message') if isinstance(body, dict) else str(body)
        raise ZohoBooksError(
            f'Zoho Books {method.upper()} {resource} HTTP {response.status_code}: {message}',
        )
    if isinstance(body, dict) and body.get('code') not in (0, None):
        raise ZohoBooksError(
            f'Zoho Books {method.upper()} {resource}: {body.get("message") or body}',
        )
    return body if isinstance(body, dict) else {'data': body}


def books_get_contact(contact_id: str, *, store=None) -> dict | None:
    contact_id = (contact_id or '').strip()
    if not contact_id:
        return None
    try:
        payload = _books_request('GET', f'contacts/{contact_id}', store=store)
    except ZohoBooksError:
        return None
    contact = payload.get('contact')
    return contact if isinstance(contact, dict) else None


def books_find_contact_id_by_email(email: str, *, store=None) -> str | None:
    normalized = (email or '').strip().lower()
    if not normalized:
        return None
    payload = _books_request(
        'GET',
        'contacts',
        store=store,
        query={'email': normalized},
    )
    contacts = payload.get('contacts') or []
    for row in contacts:
        if not isinstance(row, dict):
            continue
        emails = row.get('email') or row.get('contact_persons') or []
        if isinstance(emails, str) and emails.lower() == normalized:
            return str(row.get('contact_id') or '').strip() or None
        if isinstance(emails, list):
            for entry in emails:
                if isinstance(entry, dict):
                    addr = (entry.get('email') or '').strip().lower()
                else:
                    addr = str(entry or '').strip().lower()
                if addr == normalized:
                    return str(row.get('contact_id') or '').strip() or None
        primary = (row.get('email') or '').strip().lower()
        if primary == normalized:
            return str(row.get('contact_id') or '').strip() or None
    return None


def books_find_contact_id_by_name(contact_name: str, *, store=None) -> str | None:
    normalized = (contact_name or '').strip()
    if not normalized:
        return None
    normalized_lower = normalized.lower()

    def _match_from_payload(payload: dict) -> str | None:
        contacts = payload.get('contacts') or []
        for row in contacts:
            if not isinstance(row, dict):
                continue
            name = (row.get('contact_name') or '').strip()
            if name.lower() == normalized_lower:
                return str(row.get('contact_id') or '').strip() or None
        if len(contacts) == 1 and isinstance(contacts[0], dict):
            return str(contacts[0].get('contact_id') or '').strip() or None
        return None

    for query in (
        {'contact_name': normalized, 'contact_type': 'customer'},
        {'search_text': normalized, 'contact_type': 'customer'},
    ):
        try:
            payload = _books_request('GET', 'contacts', store=store, query=query)
        except ZohoBooksError:
            continue
        contact_id = _match_from_payload(payload)
        if contact_id:
            return contact_id
    return None


def books_create_contact(
    *,
    contact_name: str,
    email: str,
    phone: str = '',
    billing_address: dict[str, str] | None = None,
    store=None,
) -> str:
    body: dict[str, Any] = {
        'contact_name': contact_name,
        'contact_type': 'customer',
        'customer_sub_type': 'individual',
    }
    if email:
        body['email'] = email
    if phone:
        body['phone'] = phone
    if billing_address:
        body['billing_address'] = billing_address

    payload = _books_request('POST', 'contacts', store=store, json_data=body)
    contact = payload.get('contact') or {}
    contact_id = str(contact.get('contact_id') or '').strip()
    if not contact_id:
        raise ZohoBooksError('Zoho Books did not return contact_id after creating contact.')
    return contact_id


def books_create_sales_order(salesorder_body: dict[str, Any], *, store=None) -> dict[str, Any]:
    payload = _books_request('POST', 'salesorders', store=store, json_data=salesorder_body)
    salesorder = payload.get('salesorder')
    if not isinstance(salesorder, dict):
        raise ZohoBooksError('Zoho Books did not return salesorder payload.')
    return salesorder


def books_get_sales_order(salesorder_id: str, *, store=None) -> dict[str, Any]:
    salesorder_id = (salesorder_id or '').strip()
    if not salesorder_id:
        raise ZohoBooksError('salesorder_id is required.')
    payload = _books_request('GET', f'salesorders/{salesorder_id}', store=store)
    salesorder = payload.get('salesorder')
    if not isinstance(salesorder, dict):
        raise ZohoBooksError('Zoho Books did not return salesorder payload.')
    return salesorder


def books_update_sales_order(
    salesorder_id: str,
    salesorder_body: dict[str, Any],
    *,
    store=None,
) -> dict[str, Any]:
    salesorder_id = (salesorder_id or '').strip()
    if not salesorder_id:
        raise ZohoBooksError('salesorder_id is required to update sales order.')
    payload = _books_request(
        'PUT',
        f'salesorders/{salesorder_id}',
        store=store,
        json_data=salesorder_body,
    )
    salesorder = payload.get('salesorder')
    if not isinstance(salesorder, dict):
        raise ZohoBooksError('Zoho Books did not return salesorder payload after update.')
    return salesorder


def books_void_sales_order(salesorder_id: str, *, store=None) -> dict[str, Any]:
    """Mark a Zoho Books sales order as void."""
    salesorder_id = (salesorder_id or '').strip()
    if not salesorder_id:
        raise ZohoBooksError('salesorder_id is required to void sales order.')
    payload = _books_request(
        'POST',
        f'salesorders/{salesorder_id}/status/void',
        store=store,
    )
    salesorder = payload.get('salesorder')
    return salesorder if isinstance(salesorder, dict) else payload


def books_create_invoice(invoice_body: dict[str, Any], *, store=None) -> dict[str, Any]:
    payload = _books_request('POST', 'invoices', store=store, json_data=invoice_body)
    invoice = payload.get('invoice')
    if not isinstance(invoice, dict):
        raise ZohoBooksError('Zoho Books did not return invoice payload.')
    return invoice


def books_create_invoice_from_sales_order(
    salesorder_id: str,
    *,
    store=None,
    json_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a Zoho Books invoice from an existing sales order."""
    salesorder_id = (salesorder_id or '').strip()
    if not salesorder_id:
        raise ZohoBooksError('salesorder_id is required to create invoice from sales order.')
    payload = _books_request(
        'POST',
        'invoices/fromsalesorder',
        store=store,
        query={'salesorder_id': salesorder_id},
        json_data=json_data or {},
    )
    invoice = payload.get('invoice')
    if not isinstance(invoice, dict):
        raise ZohoBooksError('Zoho Books did not return invoice payload from sales order.')
    return invoice


def books_mark_invoice_sent(invoice_id: str, *, store=None) -> dict[str, Any]:
    """Mark a Zoho Books invoice as sent (POST .../invoices/{id}/status/sent)."""
    invoice_id = (invoice_id or '').strip()
    if not invoice_id:
        raise ZohoBooksError('invoice_id is required to mark invoice sent.')
    payload = _books_request('POST', f'invoices/{invoice_id}/status/sent', store=store)
    invoice = payload.get('invoice')
    return invoice if isinstance(invoice, dict) else payload


def books_create_customer_payment(payment_body: dict[str, Any], *, store=None) -> dict[str, Any]:
    payload = _books_request('POST', 'customerpayments', store=store, json_data=payment_body)
    payment = payload.get('payment')
    if not isinstance(payment, dict):
        raise ZohoBooksError('Zoho Books did not return payment payload.')
    return payment
