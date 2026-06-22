"""Product image URL helpers — avoid per-request Zoho API calls on list/cart paths."""

from __future__ import annotations

from urllib.parse import quote

from django.conf import settings

from catalog.models import Product, Store


def is_zoho_cdn_url(value: str) -> bool:
    raw = (value or '').strip().lower()
    return raw.startswith('https://cdn1.zohoecommerce.com/')


def is_usable_image_url(value: str) -> bool:
    raw = (value or '').strip()
    if not raw:
        return False
    if '/api/shop/zoho-products/' in raw:
        return False
    return raw.startswith('http://') or raw.startswith('https://') or raw.startswith('/')


def image_placeholder_url() -> str:
    return (getattr(settings, 'ZOHO_IMAGE_PLACEHOLDER_URL', '') or '').strip()


def build_zoho_cdn_product_document_url(store_domain: str, payload: dict) -> str:
    domain = (store_domain or '').strip().replace('https://', '').replace('http://', '')
    if not domain or not isinstance(payload, dict):
        return ''
    source = payload.get('product') if isinstance(payload.get('product'), dict) else payload
    if not isinstance(source, dict):
        return ''
    rows = source.get('documents') or source.get('attachments') or source.get('images') or []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_document_id = str(
                row.get('document_id')
                or row.get('image_document_id')
                or row.get('image_id')
                or row.get('id')
                or ''
            ).strip()
            if row_document_id:
                return (
                    f'https://cdn1.zohoecommerce.com/category-images/{quote(row_document_id)}/800x800'
                    f'?storefront_domain={domain}'
                )
    return ''


def extract_image_url_from_zoho_raw(raw: dict, store: Store) -> str:
    """Best-effort image URL from a Zoho Commerce list/detail product dict."""
    if not isinstance(raw, dict):
        return ''

    from zoho_integration.views import _extract_image_url, build_image_url

    direct = _extract_image_url(raw)
    if direct:
        if direct.startswith(('http://', 'https://')):
            return direct[:500]
        domain = (getattr(store, 'zoho_store_domain', '') or '').strip()
        built = build_image_url(domain, direct)
        return (built or direct)[:500]

    doc_id = str(raw.get('image_document_id') or '').strip()
    domain = (getattr(store, 'zoho_store_domain', '') or '').strip()
    if doc_id and domain:
        return (
            f'https://cdn1.zohoecommerce.com/category-images/{quote(doc_id)}/800x800'
            f'?storefront_domain={domain}'
        )[:500]

    cdn = build_zoho_cdn_product_document_url(domain, raw)
    return cdn[:500] if cdn else ''


def _zoho_account_for_store(store: Store):
    org_id = (getattr(store, 'zoho_org_id', '') or '').strip()
    if not org_id:
        return None, ''
    from zoho_integration.models import ZohoCommerceAccount

    account = ZohoCommerceAccount.objects.filter(
        is_active=True,
        organization_id=org_id,
    ).first()
    return account, org_id


def fetch_zoho_product_image_url(product: Product) -> str:
    """Fetch a product image URL from Zoho Commerce product detail."""
    store = getattr(product, 'store', None)
    zoho_pid = (getattr(product, 'zoho_product_id', '') or '').strip()
    if store is None or not zoho_pid:
        return ''

    store_domain = (getattr(store, 'zoho_store_domain', '') or '').strip()
    if not store_domain:
        return ''

    account, org_id = _zoho_account_for_store(store)
    if account is None or not org_id:
        return ''

    from zoho_integration.services import ZohoCommerceService as ZohoAccountService

    try:
        detail = ZohoAccountService(account).get_product_detail(
            organization_id=org_id,
            product_id=zoho_pid,
        )
    except Exception:
        return ''

    cdn = build_zoho_cdn_product_document_url(store_domain, detail)
    if cdn:
        return cdn[:500]

    detail_product = (
        (detail or {}).get('product')
        or (detail or {}).get('item')
        or (detail or {}).get('data')
        or detail
        or {}
    )
    if isinstance(detail_product, dict):
        return extract_image_url_from_zoho_raw(detail_product, store)
    return ''


def product_needs_image_backfill(product: Product, *, force: bool = False) -> bool:
    if not (getattr(product, 'zoho_product_id', '') or '').strip():
        return False
    if force:
        return True
    current = (getattr(product, 'image_url', '') or '').strip()
    if is_zoho_cdn_url(current):
        return False
    if current.startswith(('http://', 'https://')) and '/api/shop/zoho-products/' not in current:
        return False
    return True


def backfill_product_image(
    product: Product,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[str, str]:
    """
    Fetch image from Zoho and persist on Product when found.

    Returns (status, message) where status is updated|skipped|failed|dry_run.
    """
    if not product_needs_image_backfill(product, force=force):
        return 'skipped', 'Image already set or no Zoho product id.'

    store = getattr(product, 'store', None)
    if store is None:
        return 'failed', 'Product has no store.'

    account, org_id = _zoho_account_for_store(store)
    if account is None or not org_id:
        return 'failed', 'Store missing Zoho org/account configuration.'
    if not (getattr(store, 'zoho_store_domain', '') or '').strip():
        return 'failed', 'Store missing zoho_store_domain.'

    image_url = fetch_zoho_product_image_url(product)
    if not image_url or not is_usable_image_url(image_url):
        return 'failed', 'Zoho returned no usable image URL.'

    if dry_run:
        return 'dry_run', image_url

    if (product.image_url or '').strip() == image_url:
        return 'skipped', 'Image URL unchanged.'

    product.image_url = image_url[:500]
    product.save(update_fields=['image_url'])
    return 'updated', image_url


def product_display_image_url(
    product: Product,
    *,
    allow_zoho_fetch: bool = False,
) -> str:
    """
    Resolve image for API responses.

    List/cart paths should pass allow_zoho_fetch=False (DB URL or placeholder only).
    """
    current = (getattr(product, 'image_url', '') or '').strip()
    if is_zoho_cdn_url(current) or (
        current.startswith(('http://', 'https://')) and '/api/shop/zoho-products/' not in current
    ):
        return current

    store = getattr(product, 'store', None)
    zoho_pid = (getattr(product, 'zoho_product_id', '') or '').strip()
    if allow_zoho_fetch and store and zoho_pid:
        cdn = fetch_zoho_product_image_url(product)
        if cdn:
            return cdn

    if current and current.startswith('/'):
        domain = (getattr(store, 'zoho_store_domain', '') or '').strip() if store else ''
        if domain:
            from zoho_integration.views import build_image_url

            built = build_image_url(domain, current)
            if built:
                return built

    placeholder = image_placeholder_url()
    return placeholder
