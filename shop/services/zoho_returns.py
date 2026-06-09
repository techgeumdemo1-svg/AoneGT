"""Push approved order returns to Zoho (Commerce, Inventory, or Books credit note)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone as dj_tz

from shop.models import Order, OrderItem, OrderReturn
from shop.serializers import order_code_for_order
from shop.services.zoho_books import (
    ZohoBooksError,
    books_create_credit_note,
    books_find_sales_order,
    books_get_invoice,
    books_get_sales_order,
    store_has_books_config,
    zoho_books_organization_id,
)
from shop.services.zoho_commerce import ZohoCommerceError, ZohoCommerceService

logger = logging.getLogger(__name__)


class ZohoSalesReturnError(Exception):
    """Zoho sales return API failure."""


def zoho_sales_return_enabled() -> bool:
    return getattr(settings, 'ZOHO_SALES_RETURN_ENABLED', True)


def _books_base_host() -> str:
    return (getattr(settings, 'ZOHO_API_BASE_HOST', 'https://www.zohoapis.com') or '').rstrip('/')


def _inventory_organization_id(store) -> str:
    org = (getattr(settings, 'ZOHO_INVENTORY_ORGANIZATION_ID', '') or '').strip()
    if org:
        return org
    return zoho_books_organization_id(store=store)


def _return_reason_text(ret: OrderReturn) -> str:
    if ret.return_reason:
        try:
            return ret.get_return_reason_display()
        except Exception:
            pass
    detail = (ret.return_reason_detail or '').strip()
    if detail:
        return detail[:200]
    note = (ret.note or '').strip()
    if note:
        return note[:200]
    return 'Customer return'


def persist_books_sales_order_line_item_ids(order: Order, salesorder: dict) -> None:
    """Map Zoho Books sales order line_item_id values onto local OrderItem rows."""
    zoho_lines = salesorder.get('line_items') or []
    if not isinstance(zoho_lines, list):
        return

    local_items = [
        item
        for item in order.items.select_related('product').order_by('id')
        if (item.product_name or '').strip().lower() != 'shipping'
    ]
    used_local: set[int] = set()

    for zline in zoho_lines:
        if not isinstance(zline, dict):
            continue
        name = (zline.get('name') or '').strip().lower()
        if name == 'shipping':
            continue
        zoho_line_id = str(zline.get('line_item_id') or zline.get('salesorder_item_id') or '').strip()
        if not zoho_line_id:
            continue

        matched = None
        zoho_item_id = str(zline.get('item_id') or '').strip()
        for local in local_items:
            if local.pk in used_local:
                continue
            local_zoho_id = ''
            if local.product_id and local.product:
                local_zoho_id = (local.product.zoho_product_id or '').strip()
            if zoho_item_id and local_zoho_id == zoho_item_id:
                matched = local
                break
        if matched is None:
            for local in local_items:
                if local.pk not in used_local:
                    matched = local
                    break
        if matched is None:
            continue
        used_local.add(matched.pk)
        if matched.zoho_line_item_id != zoho_line_id:
            OrderItem.objects.filter(pk=matched.pk).update(zoho_line_item_id=zoho_line_id[:120])


def _inventory_get_sales_order(salesorder_id: str, *, store) -> dict[str, Any]:
    payload = _inventory_request('GET', f'salesorders/{salesorder_id}', store=store)
    salesorder = payload.get('salesorder')
    if isinstance(salesorder, dict):
        return salesorder
    if isinstance(payload, dict) and payload.get('salesorder_id'):
        return payload
    raise ZohoSalesReturnError('Zoho Inventory did not return salesorder payload.')


def _fetch_sales_order_for_return(order: Order) -> dict[str, Any]:
    """Resolve the Zoho sales order used to map return line ids."""
    books_salesorder_id = (order.zoho_books_salesorder_id or '').strip()
    if not books_salesorder_id:
        raise ZohoSalesReturnError('Order has no Zoho Books sales order id.')

    errors: list[str] = []

    try:
        return books_get_sales_order(books_salesorder_id, store=order.store)
    except ZohoBooksError as exc:
        message = str(exc)
        errors.append(message)
        if '404' not in message and 'does not exist' not in message.lower():
            raise ZohoSalesReturnError(message) from exc

    try:
        return _inventory_get_sales_order(books_salesorder_id, store=order.store)
    except ZohoSalesReturnError as exc:
        errors.append(str(exc))

    found = books_find_sales_order(
        store=order.store,
        reference_number=order_code_for_order(order),
        salesorder_number=(order.zoho_books_salesorder_number or '').strip(),
    )
    if found:
        resolved_id = str(found.get('salesorder_id') or '').strip()
        if resolved_id and resolved_id != books_salesorder_id:
            order.zoho_books_salesorder_id = resolved_id[:64]
            number = str(found.get('salesorder_number') or '').strip()
            if number:
                order.zoho_books_salesorder_number = number[:64]
            order.save(
                update_fields=[
                    'zoho_books_salesorder_id',
                    'zoho_books_salesorder_number',
                    'updated_at',
                ],
            )
        return found

    joined = ' | '.join(errors) if errors else 'Sales order not found in Zoho.'
    raise ZohoSalesReturnError(
        f'Could not load Zoho sales order {books_salesorder_id} for order #{order.pk}. {joined}',
    )


def _ensure_order_line_item_ids(order: Order) -> None:
    missing = order.items.filter(zoho_line_item_id='').exists()
    if not missing:
        return

    if not store_has_books_config(order.store):
        return

    salesorder = _fetch_sales_order_for_return(order)
    persist_books_sales_order_line_item_ids(order, salesorder)


def _prefer_books_credit_note_for_return(order: Order) -> bool:
    if not (order.zoho_books_invoice_id or '').strip():
        return False
    setting = (getattr(settings, 'ZOHO_RETURN_PREFER_BOOKS_CREDIT_NOTE', '') or '').strip().lower()
    if setting in ('true', '1', 'yes'):
        return True
    if setting in ('false', '0', 'no'):
        return False
    return getattr(settings, 'ZOHO_BOOKS_MANUAL_WORKFLOW', False)


def _books_credit_note_fallback_enabled() -> bool:
    return getattr(settings, 'ZOHO_RETURN_BOOKS_CREDIT_NOTE_FALLBACK', True)


def _inventory_error_allows_books_fallback(exc: ZohoSalesReturnError) -> bool:
    message = str(exc).lower()
    return 'account is disabled' in message or 'inventory' in message and 'http 401' in message


def _find_invoice_line_for_order_item(
    invoice_lines: list[dict[str, Any]],
    order_item: OrderItem,
) -> dict[str, Any] | None:
    salesorder_line_id = (order_item.zoho_line_item_id or '').strip()
    product_zoho_id = ''
    if order_item.product_id and order_item.product:
        product_zoho_id = (order_item.product.zoho_product_id or '').strip()
    product_name = (order_item.product_name or '').strip().lower()

    for row in invoice_lines:
        if not isinstance(row, dict):
            continue
        if salesorder_line_id:
            so_line = str(row.get('salesorder_item_id') or '').strip()
            if so_line and so_line == salesorder_line_id:
                return row
    for row in invoice_lines:
        if not isinstance(row, dict):
            continue
        zoho_item_id = str(row.get('item_id') or '').strip()
        if product_zoho_id and zoho_item_id == product_zoho_id:
            return row
    for row in invoice_lines:
        if not isinstance(row, dict):
            continue
        name = (row.get('name') or '').strip().lower()
        if product_name and name == product_name:
            return row
    return None


def _build_books_credit_note_line_items(
    *,
    order: Order,
    order_return: OrderReturn,
    invoice: dict[str, Any],
) -> list[dict[str, Any]]:
    invoice_id = (order.zoho_books_invoice_id or '').strip()
    if not invoice_id:
        raise ZohoSalesReturnError('Order has no Zoho Books invoice id for credit note return.')

    invoice_lines = [
        row for row in (invoice.get('line_items') or [])
        if isinstance(row, dict) and (row.get('name') or '').strip().lower() != 'shipping'
    ]
    rows: list[dict[str, Any]] = []
    for line in order_return.lines.select_related('order_item__product').all():
        order_item = line.order_item
        invoice_line = _find_invoice_line_for_order_item(invoice_lines, order_item)
        if invoice_line is None:
            raise ZohoSalesReturnError(
                f'Could not match order item {order_item.pk} to invoice {invoice_id} line items.',
            )

        invoice_item_id = str(invoice_line.get('line_item_id') or '').strip()
        if not invoice_item_id:
            raise ZohoSalesReturnError(
                f'Invoice line for order item {order_item.pk} is missing line_item_id.',
            )

        qty = int(line.quantity or 0)
        if qty <= 0:
            continue

        row: dict[str, Any] = {
            'name': invoice_line.get('name') or order_item.product_name,
            'rate': invoice_line.get('rate') or order_item.unit_price,
            'quantity': qty,
        }
        account_id = str(invoice_line.get('account_id') or '').strip()
        if account_id:
            row['account_id'] = account_id
        item_id = str(invoice_line.get('item_id') or '').strip()
        if item_id:
            row['item_id'] = item_id
        tax_id = str(invoice_line.get('tax_id') or '').strip()
        if tax_id:
            row['tax_id'] = tax_id
        rows.append(row)

    if not rows:
        raise ZohoSalesReturnError('Return has no line items to push to Zoho Books credit note.')
    return rows


def _parse_creditnote_id(creditnote: dict[str, Any]) -> str:
    creditnote_id = str(creditnote.get('creditnote_id') or '').strip()
    if not creditnote_id:
        raise ZohoSalesReturnError('Zoho creditnote_id missing in response.')
    return creditnote_id


def _create_books_credit_note_for_return(
    *,
    order: Order,
    order_return: OrderReturn,
) -> str:
    invoice_id = (order.zoho_books_invoice_id or '').strip()
    if not invoice_id:
        raise ZohoSalesReturnError(
            'Order has no Zoho Books invoice. Cannot create credit note for return.',
        )

    try:
        invoice = books_get_invoice(invoice_id, store=order.store)
        line_items = _build_books_credit_note_line_items(
            order=order,
            order_return=order_return,
            invoice=invoice,
        )
        body: dict[str, Any] = {
            'customer_id': invoice.get('customer_id'),
            'date': order_return.created_at.date().isoformat(),
            'reference_number': f'{order_code_for_order(order)}-RET{order_return.pk}'[:100],
            'line_items': line_items,
            'notes': _return_reason_text(order_return)[:500],
        }
        place_of_supply = str(invoice.get('place_of_supply') or '').strip()
        if place_of_supply:
            body['place_of_supply'] = place_of_supply
        currency_id = str(invoice.get('currency_id') or '').strip()
        if currency_id:
            body['currency_id'] = currency_id
        tax_treatment = str(invoice.get('tax_treatment') or '').strip()
        if tax_treatment:
            body['tax_treatment'] = tax_treatment
        if not body.get('customer_id'):
            raise ZohoSalesReturnError('Zoho Books invoice is missing customer_id.')
        creditnote = books_create_credit_note(
            body,
            store=order.store,
            invoice_id=invoice_id,
        )
    except ZohoBooksError as exc:
        message = str(exc)
        if 'creditnotes' in message.lower() and '401' in message:
            message = (
                f'{message} Add ZohoBooks.creditnotes.CREATE to your Zoho refresh token scopes.'
            )
        raise ZohoSalesReturnError(message) from exc
    return _parse_creditnote_id(creditnote)


def _resolve_salesorder_id(order: Order) -> tuple[str, str]:
    commerce_id = (order.zoho_salesorder_id or '').strip()
    if commerce_id:
        return commerce_id, 'commerce'
    books_id = (order.zoho_books_salesorder_id or '').strip()
    if books_id:
        return books_id, 'inventory'
    return '', ''


def _build_return_line_items(order_return: OrderReturn) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in order_return.lines.select_related('order_item__product').all():
        order_item = line.order_item
        salesorder_item_id = (order_item.zoho_line_item_id or '').strip()
        if not salesorder_item_id:
            raise ZohoSalesReturnError(
                f'Order item {order_item.pk} is missing zoho_line_item_id. '
                'Sync the sales order to Zoho before approving the return.',
            )

        zoho_item_id = ''
        if order_item.product_id and order_item.product:
            zoho_item_id = (order_item.product.zoho_product_id or '').strip()
        if not zoho_item_id:
            raise ZohoSalesReturnError(
                f'Product for order item {order_item.pk} is missing zoho_product_id.',
            )

        rows.append({
            'salesorder_item_id': salesorder_item_id,
            'item_id': zoho_item_id,
            'quantity': int(line.quantity or 0),
        })

    if not rows:
        raise ZohoSalesReturnError('Return has no line items to push to Zoho.')
    return rows


def _parse_salesreturn_id(response: Any) -> str:
    if not isinstance(response, dict):
        raise ZohoSalesReturnError('Unexpected Zoho sales return response type.')

    code = response.get('code')
    if code not in (0, '0', None):
        raise ZohoSalesReturnError(str(response.get('message') or response))

    salesreturn = response.get('salesreturn') or response.get('salesreturns')
    if isinstance(salesreturn, list) and salesreturn:
        salesreturn = salesreturn[0]
    if not isinstance(salesreturn, dict):
        raise ZohoSalesReturnError('Zoho did not return salesreturn payload.')

    salesreturn_id = str(salesreturn.get('salesreturn_id') or '').strip()
    if not salesreturn_id:
        raise ZohoSalesReturnError('Zoho salesreturn_id missing in response.')
    return salesreturn_id


def _create_commerce_sales_return(
    *,
    order: Order,
    order_return: OrderReturn,
    salesorder_id: str,
    line_items: list[dict[str, Any]],
) -> str:
    body = {
        'reason': _return_reason_text(order_return)[:100],
        'line_items': line_items,
    }
    try:
        response = ZohoCommerceService.admin_post(
            'salesreturns',
            body,
            store=order.store,
            query={'salesorder_id': salesorder_id},
        )
    except ZohoCommerceError as exc:
        raise ZohoSalesReturnError(str(exc)) from exc
    return _parse_salesreturn_id(response)


def _inventory_request(
    method: str,
    resource: str,
    *,
    store,
    query: dict[str, Any] | None = None,
    json_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    org_id = _inventory_organization_id(store)
    if not org_id:
        raise ZohoSalesReturnError(
            'Set ZOHO_INVENTORY_ORGANIZATION_ID or store Zoho Books org for Inventory sales returns.',
        )

    q = dict(query or {})
    q.setdefault('organization_id', org_id)
    resource = (resource or '').strip().lstrip('/')
    url = f'{_books_base_host()}/inventory/v1/{resource}?{urlencode(q)}'
    headers = {
        'Authorization': f'Zoho-oauthtoken {ZohoCommerceService.refresh_access_token(store)}',
    }
    if json_data is not None:
        headers['Content-Type'] = 'application/json'

    try:
        response = requests.request(
            method.upper(),
            url,
            headers=headers,
            json=json_data,
            timeout=60,
        )
    except requests.RequestException as exc:
        raise ZohoSalesReturnError(f'Zoho Inventory request failed: {exc}') from exc

    try:
        body = response.json()
    except ValueError:
        body = {'raw': response.text[:2000]}

    if response.status_code >= 400:
        message = body.get('message') if isinstance(body, dict) else str(body)
        if response.status_code == 401:
            message = (
                f'{message}. Regenerate the Zoho refresh token with scopes '
                'ZohoInventory.salesreturns.CREATE and ZohoInventory.salesorders.READ.'
            )
        elif response.status_code == 400 and 'account is disabled' in str(message).lower():
            message = (
                f'{message} Enable Zoho Inventory for organization '
                f'{org_id} in Zoho (subscription + active user), or use a Books-only return flow.'
            )
        raise ZohoSalesReturnError(
            f'Zoho Inventory {method.upper()} {resource} HTTP {response.status_code}: {message}',
        )
    if isinstance(body, dict) and body.get('code') not in (0, None):
        raise ZohoSalesReturnError(
            f'Zoho Inventory {method.upper()} {resource}: {body.get("message") or body}',
        )
    return body if isinstance(body, dict) else {'data': body}


def _create_inventory_sales_return(
    *,
    order: Order,
    order_return: OrderReturn,
    salesorder_id: str,
    line_items: list[dict[str, Any]],
) -> str:
    body = {
        'date': order_return.created_at.date().isoformat(),
        'reason': _return_reason_text(order_return)[:200],
        'line_items': line_items,
    }
    response = _inventory_request(
        'POST',
        'salesreturns',
        store=order.store,
        query={'salesorder_id': salesorder_id},
        json_data=body,
    )
    return _parse_salesreturn_id(response)


def create_zoho_sales_return_for_order_return(order_return: OrderReturn) -> str:
    """Create a Zoho sales return for an approved local return. Raises ZohoSalesReturnError."""
    order_return = (
        OrderReturn.objects.select_related('order', 'order__store', 'user')
        .prefetch_related('lines__order_item__product')
        .get(pk=order_return.pk)
    )
    order = order_return.order
    salesorder_id, source = _resolve_salesorder_id(order)
    if not salesorder_id:
        raise ZohoSalesReturnError(
            'Order has no Zoho sales order id (Commerce or Books). Cannot create sales return.',
        )

    _ensure_order_line_item_ids(order)
    order.refresh_from_db(
        fields=[
            'zoho_books_salesorder_id',
            'zoho_books_salesorder_number',
            'zoho_books_invoice_id',
        ],
    )
    salesorder_id, source = _resolve_salesorder_id(order)
    line_items = _build_return_line_items(order_return)

    if source == 'commerce':
        return _create_commerce_sales_return(
            order=order,
            order_return=order_return,
            salesorder_id=salesorder_id,
            line_items=line_items,
        )

    if _prefer_books_credit_note_for_return(order):
        logger.info(
            'zoho-return: using Books credit note for return=%s invoice=%s',
            order_return.pk,
            order.zoho_books_invoice_id,
        )
        return _create_books_credit_note_for_return(order=order, order_return=order_return)

    try:
        return _create_inventory_sales_return(
            order=order,
            order_return=order_return,
            salesorder_id=salesorder_id,
            line_items=line_items,
        )
    except ZohoSalesReturnError as exc:
        if (
            _books_credit_note_fallback_enabled()
            and _inventory_error_allows_books_fallback(exc)
            and (order.zoho_books_invoice_id or '').strip()
        ):
            logger.info(
                'zoho-return: inventory failed, falling back to Books credit note return=%s',
                order_return.pk,
            )
            return _create_books_credit_note_for_return(order=order, order_return=order_return)
        raise


def _strip_zoho_sync_failure_notes(note: str) -> str:
    kept: list[str] = []
    for line in (note or '').splitlines():
        if line.strip().startswith('[Zoho sync failed]'):
            continue
        kept.append(line)
    return '\n'.join(kept).strip()


def _record_return_sync_success(order_return: OrderReturn, zoho_id: str) -> None:
    clean = _strip_zoho_sync_failure_notes(order_return.note or '')
    stamped = f'[Zoho synced] Books credit note {zoho_id}'
    order_return.note = f'{clean}\n{stamped}'.strip() if clean else stamped


def _record_return_sync_failure(order_return: OrderReturn, message: str) -> None:
    err = str(message or 'Zoho sales return sync failed.')[:500]
    note = (order_return.note or '').strip()
    stamped = f'[Zoho sync failed] {err}'
    order_return.note = f'{note}\n{stamped}'.strip() if note else stamped
    order_return.status = OrderReturn.Status.FAILED
    order_return.save(update_fields=['status', 'note', 'updated_at'])


def maybe_push_return_to_zoho(order_return_id: int) -> None:
    """Best-effort Zoho sales return sync after admin approval. Never raises to callers."""
    if not zoho_sales_return_enabled():
        return

    try:
        with transaction.atomic():
            order_return = (
                OrderReturn.objects.select_for_update()
                .select_related('order', 'order__store')
                .get(pk=order_return_id)
            )
            if (order_return.zoho_salesreturn_id or '').strip():
                return
            if order_return.status != OrderReturn.Status.SYNCED:
                return

            salesreturn_id = create_zoho_sales_return_for_order_return(order_return)
            order_return.zoho_salesreturn_id = salesreturn_id[:120]
            _record_return_sync_success(order_return, salesreturn_id)
            order_return.save(update_fields=['zoho_salesreturn_id', 'note', 'updated_at'])
    except OrderReturn.DoesNotExist:
        return
    except ZohoSalesReturnError as exc:
        logger.exception('zoho-return: sync failed return=%s', order_return_id)
        try:
            order_return = OrderReturn.objects.get(pk=order_return_id)
            _record_return_sync_failure(order_return, str(exc))
        except OrderReturn.DoesNotExist:
            pass
    except ZohoBooksError as exc:
        logger.exception('zoho-return: books lookup failed return=%s', order_return_id)
        try:
            order_return = OrderReturn.objects.get(pk=order_return_id)
            _record_return_sync_failure(order_return, str(exc))
        except OrderReturn.DoesNotExist:
            pass
    except Exception as exc:
        logger.exception('zoho-return: unexpected error return=%s', order_return_id)
        try:
            order_return = OrderReturn.objects.get(pk=order_return_id)
            _record_return_sync_failure(order_return, str(exc))
        except OrderReturn.DoesNotExist:
            pass
    else:
        logger.info(
            'zoho-return: Zoho return document created return=%s zoho_id=%s',
            order_return_id,
            salesreturn_id,
        )


def enqueue_push_return_to_zoho(order_return_id: int) -> None:
    """Sync hook used by admin approve and customer return create (no-op until approved)."""
    maybe_push_return_to_zoho(order_return_id)
