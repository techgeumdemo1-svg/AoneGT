"""
Pull products from Zoho Commerce store API (GET /store/api/v1/products) and upsert local
:class:`~catalog.models.Product` rows keyed by stable Zoho ids (``variant_id`` when variants
exist, else ``product_id``).

OAuth: ``ZohoCommerce.items.READ`` — same headers as :mod:`catalog.services.zoho_commerce_products`.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import transaction
from django.utils.text import slugify

from catalog.models import Product, Store
from catalog.services.zoho_commerce_products import (
    ZohoCommerceProductError,
    build_products_list_url,
    zoho_commerce_proxy_get,
)


class ZohoProductSyncError(Exception):
    """Unsuccessful Zoho response or unexpected payload during catalog sync."""


def _safe_decimal(val: Any) -> Decimal | None:
    if val is None or val == '':
        return None
    try:
        return Decimal(str(val)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _description_from_zoho_product(raw: dict[str, Any]) -> str:
    parts = [
        raw.get('product_description') or '',
        raw.get('description') or '',
        raw.get('product_short_description') or '',
    ]
    text = '\n\n'.join((p.strip() for p in parts if p and str(p).strip()))
    return (text or '')[:5000]


def _variant_option_suffix(variant: dict[str, Any]) -> str:
    parts: list[str] = []
    for i in (1, 2, 3):
        data = (variant.get(f'attribute_option_data{i}') or '').strip()
        name = (variant.get(f'attribute_option_name{i}') or '').strip()
        if data:
            parts.append(data)
        elif name:
            parts.append(name)
    return ', '.join(parts)


def _variant_display_name(base_name: str, variant: dict[str, Any]) -> str:
    suffix = _variant_option_suffix(variant)
    if suffix:
        return f'{base_name} ({suffix})'
    vn = (variant.get('name') or '').strip()
    if vn and vn != base_name:
        return vn
    return base_name


def _row_active(raw: dict[str, Any], variant: dict[str, Any] | None) -> bool:
    if raw.get('show_in_storefront') is False:
        return False
    if (raw.get('status') or '').lower() != 'active':
        return False
    if variant is not None and (variant.get('status') or 'active').lower() != 'active':
        return False
    return True


def expand_zoho_list_product(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Normalize one Zoho list payload product dict into sellable rows (one per variant, or one
    synthetic row if the API omits variants).
    """
    base_name = (raw.get('name') or '').strip() or 'Product'
    product_id = str(raw.get('product_id') or '').strip()
    url_hint = (raw.get('url') or '').strip()
    category = (raw.get('category_name') or raw.get('category') or '').strip()
    desc = _description_from_zoho_product(raw)
    variants = raw.get('variants')

    rows: list[dict[str, Any]] = []
    if not variants:
        if not product_id:
            return rows
        rate = raw.get('min_rate') or raw.get('rate') or 0
        price = _safe_decimal(rate) or Decimal('0')
        compare = _safe_decimal(raw.get('max_rate'))
        if compare is not None and compare <= 0:
            compare = None
        rows.append({
            'zoho_product_id': product_id,
            'name': base_name,
            'slug_hint': url_hint or base_name,
            'sku': (raw.get('sku') or '').strip(),
            'price': price,
            'compare_at_price': compare,
            'description': desc,
            'category': category[:255] if category else '',
            'is_active': _row_active(raw, None),
        })
        return rows

    for v in variants:
        if not isinstance(v, dict):
            continue
        vid = str(v.get('variant_id') or '').strip()
        if not vid:
            continue
        price = _safe_decimal(v.get('rate')) or Decimal('0')
        compare = _safe_decimal(v.get('label_rate'))
        if compare is not None and compare > 0 and compare <= price:
            compare = None
        rows.append({
            'zoho_product_id': vid,
            'name': _variant_display_name(base_name, v),
            'slug_hint': url_hint or base_name,
            'sku': (v.get('sku') or '').strip()[:120],
            'price': price,
            'compare_at_price': compare,
            'description': desc,
            'category': category[:255] if category else '',
            'is_active': _row_active(raw, v),
        })
    if not rows and product_id:
        rate = raw.get('min_rate') or raw.get('rate') or 0
        price = _safe_decimal(rate) or Decimal('0')
        compare = _safe_decimal(raw.get('max_rate'))
        if compare is not None and compare <= 0:
            compare = None
        if compare is not None and compare <= price:
            compare = None
        rows.append({
            'zoho_product_id': product_id,
            'name': base_name,
            'slug_hint': url_hint or base_name,
            'sku': (raw.get('sku') or '').strip()[:120],
            'price': price,
            'compare_at_price': compare,
            'description': desc,
            'category': category[:255] if category else '',
            'is_active': _row_active(raw, None),
        })
    return rows


def _resolve_unique_slug(store: Store, base: str, zoho_id: str, product: Product | None) -> str:
    root = slugify(base)[:180] or 'item'
    zpart = slugify(zoho_id.replace('/', '-'))[:80] or 'id'
    for candidate in (f'{root}-{zpart}', f'{root}-{zpart}-store{store.pk}'):
        slug = candidate[:255]
        qs = Product.objects.filter(store=store, slug=slug)
        if product is not None:
            qs = qs.exclude(pk=product.pk)
        if not qs.exists():
            return slug
    return f'{root}-{zpart}-s{store.pk}-z{zoho_id}'[:255]


def _upsert_product(store: Store, row: dict[str, Any]) -> tuple[str, Product]:
    zid = row['zoho_product_id']
    product = Product.objects.filter(store=store, zoho_product_id=zid).first()
    slug = _resolve_unique_slug(store, row['slug_hint'], zid, product)
    defaults = {
        'name': row['name'][:255],
        'slug': slug,
        'sku': row['sku'],
        'description': row['description'],
        'price': row['price'],
        'compare_at_price': row['compare_at_price'],
        'category': row['category'],
        'is_active': row['is_active'],
    }
    if product is None:
        product = Product(store=store, zoho_product_id=zid)
        for k, v in defaults.items():
            setattr(product, k, v)
        product.save()
        return 'created', product
    changed = False
    for k, v in defaults.items():
        if getattr(product, k) != v:
            setattr(product, k, v)
            changed = True
    if changed:
        product.save()
        return 'updated', product
    return 'unchanged', product


def _parse_list_response(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ZohoProductSyncError(f'Unexpected response type: {type(payload).__name__}')
    code = payload.get('code')
    try:
        ok = int(code) == 0
    except (TypeError, ValueError):
        ok = code in (0, '0', None)
    if not ok:
        msg = payload.get('message') or payload.get('error') or str(payload)
        raise ZohoProductSyncError(f'Zoho products API error: {msg}')
    products = payload.get('products')
    if products is None:
        raise ZohoProductSyncError('Zoho response missing "products" array.')
    if not isinstance(products, list):
        raise ZohoProductSyncError('Zoho "/products" is not a list.')
    page_ctx = payload.get('page_context') if isinstance(payload.get('page_context'), dict) else {}
    return products, page_ctx


def sync_store_from_zoho(
    store: Store,
    *,
    filter_by: str = 'Status.Active',
    per_page: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Fetch all pages from Zoho for one local :class:`~catalog.models.Store` and upsert products.

    :param store: Must be able to authenticate via store fields or ``ZOHO_*`` env (see proxy).
    :param filter_by: Zoho ``filter_by`` query (e.g. ``Status.Active``, ``Status.All``).
    :param per_page: Page size (Zoho allows 10, 25, 50, 100, 200).
    :param dry_run: If True, parse and count rows but do not write the database.
    :returns: Stats dict with keys ``pages``, ``raw_products``, ``rows``, ``created``, ``updated``,
              ``unchanged``, ``dry_run``, and optional ``errors`` (list of str).
    """
    allowed = (10, 25, 50, 100, 200)
    per_page = int(per_page)
    if per_page not in allowed:
        for v in allowed:
            if v >= per_page:
                per_page = v
                break
        else:
            per_page = 200

    stats: dict[str, Any] = {
        'pages': 0,
        'raw_products': 0,
        'rows': 0,
        'created': 0,
        'updated': 0,
        'unchanged': 0,
        'dry_run': dry_run,
        'errors': [],
    }
    page = 1
    while True:
        url = build_products_list_url({
            'filter_by': filter_by,
            'page_start_from': page,
            'per_page': per_page,
            'sort_column': 'name',
            'sort_order': 'A',
        })
        try:
            status, payload = zoho_commerce_proxy_get(url, store=store)
        except ZohoCommerceProductError as e:
            raise ZohoProductSyncError(str(e)) from e

        if status != 200:
            raise ZohoProductSyncError(
                f'Zoho HTTP {status}: {payload if isinstance(payload, str) else payload!r}',
            )

        products, page_ctx = _parse_list_response(payload)
        stats['pages'] += 1
        stats['raw_products'] += len(products)

        batch_rows: list[dict[str, Any]] = []
        for raw in products:
            if not isinstance(raw, dict):
                stats['errors'].append('Skipped non-object product row')
                continue
            try:
                batch_rows.extend(expand_zoho_list_product(raw))
            except Exception as e:
                stats['errors'].append(f'expand error {raw.get("product_id")}: {e}')

        stats['rows'] += len(batch_rows)

        if not dry_run and batch_rows:
            with transaction.atomic():
                for row in batch_rows:
                    try:
                        action, _p = _upsert_product(store, row)
                        stats[action] += 1
                    except Exception as e:
                        stats['errors'].append(f'upsert {row.get("zoho_product_id")}: {e}')

        has_more = bool(page_ctx.get('has_more_page'))
        if not has_more:
            break
        page += 1
        if page > 10000:
            stats['errors'].append('Stopped after 10000 pages (safety).')
            break

    if dry_run:
        stats['created'] = stats['updated'] = stats['unchanged'] = 0

    return stats


def iter_syncable_stores(queryset=None):
    """Active stores, optionally those with per-store org id (all still get env fallback in proxy)."""
    qs = queryset or Store.objects.filter(is_active=True).order_by('pk')
    return qs
