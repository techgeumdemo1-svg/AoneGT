from decimal import Decimal
from typing import Optional, Tuple
from urllib.parse import urlparse

from django.db import transaction
from django.utils.text import slugify

from catalog.models import Product, Store
from shop.models import Cart, CartItem
from shop.serializers import CartItemSerializer
from shop.services.zoho_commerce import ZohoCommerceError, ZohoCommerceService
from zoho_integration.models import ZohoCommerceAccount
from zoho_integration.services import ZohoCommerceService as ZohoAccountService


def _as_decimal(raw, default='0'):
    try:
        return Decimal(str(raw)).quantize(Decimal('0.01'))
    except Exception:
        return Decimal(default).quantize(Decimal('0.01'))


def _normalize_zoho_store_domain(raw: str) -> str:
    s = (raw or '').strip()
    if not s:
        return ''
    if '://' not in s and '/' in s:
        s = s.split('/')[0]
    if '://' in s:
        parsed = urlparse(s)
        host = (parsed.netloc or parsed.path or '').split('/')[0]
    else:
        host = s.split('/')[0]
    return host.strip().lower()


def resolve_or_create_store_for_zoho_account(
    account: ZohoCommerceAccount,
    organization_id: str,
    primary_domain: str,
) -> Tuple[Optional[Store], Optional[str]]:
    """
    Match local catalog.Store by zoho_org_id, or create one using OAuth fields from
    ZohoCommerceAccount plus primary_domain for zoho_store_domain (domain-name header).
    """
    store = Store.objects.filter(zoho_org_id=organization_id, is_active=True).first()
    if store is not None:
        domain = _normalize_zoho_store_domain(primary_domain)
        if domain and not (store.zoho_store_domain or '').strip():
            store.zoho_store_domain = domain[:255]
            store.save(update_fields=['zoho_store_domain'])
        return store, None

    domain = _normalize_zoho_store_domain(primary_domain)
    if not domain:
        return None, (
            'No local Store for this organization_id. Pass primary_domain from '
            '/zoho/multi/stores/ for this organization, or create a Store in admin with '
            'zoho_org_id and zoho_store_domain set.'
        )

    base_slug = slugify(f'{account.name}-{organization_id}') or f'zoho-org-{organization_id}'
    slug = base_slug[:200]
    n = 0
    while Store.objects.filter(slug=slug).exists():
        n += 1
        slug = f'{base_slug[:190]}-{n}'[:255]

    store = Store.objects.create(
        name=str(account.name)[:255],
        slug=slug,
        zoho_org_id=organization_id[:120],
        zoho_store_domain=domain[:255],
        client_id=(account.client_id or '')[:255],
        client_secret=(account.client_secret or '')[:255],
        refresh_token=account.refresh_token or '',
        is_active=True,
    )
    return store, None


def _upsert_local_product_from_zoho(store: Store, zoho_product_id: str, payload: dict) -> Product:
    product_blob = payload.get('product') if isinstance(payload, dict) else None
    source = product_blob if isinstance(product_blob, dict) else payload
    if not isinstance(source, dict):
        raise ZohoCommerceError('Invalid product response from Zoho.')

    variants = source.get('variants') if isinstance(source.get('variants'), list) else []
    first_variant = variants[0] if variants and isinstance(variants[0], dict) else {}

    name = str(
        source.get('name')
        or source.get('product_name')
        or source.get('item_name')
        or first_variant.get('name')
        or f'Zoho Product {zoho_product_id}'
    ).strip()
    sku = str(
        source.get('sku')
        or first_variant.get('sku')
        or source.get('product_id')
        or first_variant.get('variant_id')
        or zoho_product_id
        or ''
    ).strip()
    category = str(source.get('category_name') or source.get('category') or '').strip()
    description = str(source.get('description') or '').strip()
    currency = str(source.get('currency_code') or source.get('currency') or 'AED').strip() or 'AED'
    price = _as_decimal(
        source.get('min_rate')
        or source.get('rate')
        or source.get('price')
        or source.get('selling_price')
        or first_variant.get('rate')
        or '0'
    )
    compare_at_price_raw = source.get('regular_price') or source.get('compare_at_price')
    if compare_at_price_raw in (None, ''):
        compare_at_price_raw = first_variant.get('label_rate')
    compare_at_price = (
        _as_decimal(compare_at_price_raw)
        if compare_at_price_raw not in (None, '')
        else None
    )
    docs = source.get('documents') if isinstance(source.get('documents'), list) else []
    first_doc = docs[0] if docs and isinstance(docs[0], dict) else {}
    variant_docs = (
        first_variant.get('documents')
        if isinstance(first_variant.get('documents'), list)
        else []
    )
    first_variant_doc = (
        variant_docs[0]
        if variant_docs and isinstance(variant_docs[0], dict)
        else {}
    )
    image_url = str(
        source.get('image_url')
        or source.get('image_name')
        or source.get('image')
        or source.get('image_path')
        or first_doc.get('image_url')
        or first_doc.get('url')
        or first_doc.get('document_url')
        or first_doc.get('download_url')
        or first_variant_doc.get('image_url')
        or first_variant_doc.get('url')
        or first_variant_doc.get('document_url')
        or first_variant_doc.get('download_url')
        or ''
    ).strip()

    product = Product.objects.filter(store=store, zoho_product_id=zoho_product_id).first()
    base_slug = slugify(name) or f'zoho-{zoho_product_id}'
    slug = base_slug[:255]
    if product is None:
        suffix = 1
        while Product.objects.filter(store=store, slug=slug).exists():
            suffix += 1
            slug = f'{base_slug[:245]}-{suffix}'[:255]
        product = Product(
            store=store,
            zoho_product_id=zoho_product_id,
            slug=slug,
        )

    fallback_name = f'Zoho Product {zoho_product_id}'
    resolved_name = name
    if (
        product.pk
        and ((name or '').strip() == fallback_name)
        and (product.name or '').strip()
        and (product.name or '').strip() != fallback_name
    ):
        resolved_name = product.name

    resolved_sku = sku[:120] if sku else (product.sku or '')
    resolved_category = category[:255] if category else (product.category or '')
    resolved_description = description if description else (product.description or '')
    resolved_currency = currency[:8] if currency else (product.currency or 'AED')
    resolved_image_url = image_url[:500] if image_url else (product.image_url or '')

    resolved_price = price
    if product.pk:
        try:
            existing_price = Decimal(str(product.price or '0'))
        except Exception:
            existing_price = Decimal('0')
        if resolved_price <= Decimal('0') and existing_price > Decimal('0'):
            resolved_price = existing_price

    resolved_compare_at_price = compare_at_price
    if resolved_compare_at_price in (None, ''):
        resolved_compare_at_price = product.compare_at_price

    product.name = resolved_name[:255]
    product.sku = resolved_sku
    product.category = resolved_category
    product.description = resolved_description
    product.price = resolved_price
    product.compare_at_price = resolved_compare_at_price
    product.currency = resolved_currency
    product.image_url = resolved_image_url
    product.is_active = True
    product.save()
    return product


def _fetch_zoho_product_from_account(
    account: ZohoCommerceAccount,
    organization_id: str,
    zoho_product_id: str,
):
    service = ZohoAccountService(account)
    data = service.list_products(organization_id=organization_id, page=1, per_page=200)
    rows = data.get('products', []) or data.get('items', [])
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get('product_id') or row.get('id') or '').strip()
        if pid == zoho_product_id:
            return row
    return None


def perform_cart_add_zoho_product(
    user,
    store: Store,
    zoho_product_id: str,
    quantity: int,
    *,
    account: Optional[ZohoCommerceAccount] = None,
    organization_id: Optional[str] = None,
):
    """Returns (response_data|None, error_detail|None, http_status)."""
    fresh_zoho_payload = None
    if account is not None and organization_id:
        try:
            fresh_zoho_payload = _fetch_zoho_product_from_account(
                account,
                organization_id,
                zoho_product_id,
            )
        except Exception:
            fresh_zoho_payload = None

    product = Product.objects.filter(
        is_active=True,
        store=store,
        zoho_product_id=zoho_product_id,
    ).first()
    if product is not None and fresh_zoho_payload is not None:
        product = _upsert_local_product_from_zoho(store, zoho_product_id, fresh_zoho_payload)
    elif product is not None and not (product.sku or '').strip():
        product.sku = zoho_product_id[:120]
        product.save(update_fields=['sku'])
    if product is None:
        try:
            zoho_payload = fresh_zoho_payload
            if zoho_payload is None:
                zoho_payload = ZohoCommerceService.get_product_detail_storefront(
                    zoho_product_id,
                    store=store,
                )
            product = _upsert_local_product_from_zoho(store, zoho_product_id, zoho_payload)
        except (ZohoCommerceError, Exception) as e:
            return None, str(e), 502
    elif not (product.image_url or '').strip():
        try:
            detail_payload = ZohoCommerceService.get_product_detail_storefront(
                zoho_product_id,
                store=store,
            )
            product = _upsert_local_product_from_zoho(store, zoho_product_id, detail_payload)
        except ZohoCommerceError:
            pass

    with transaction.atomic():
        cart, _ = Cart.objects.select_for_update().get_or_create(user=user)
        item, created = CartItem.objects.select_for_update().get_or_create(
            cart=cart,
            product=product,
            defaults={'quantity': quantity, 'store': store},
        )
        if not created:
            item.quantity += quantity
            item.store = store
            item.save(update_fields=['quantity', 'store'])

    item = CartItem.objects.select_related('product', 'store').get(pk=item.pk)
    return CartItemSerializer(item).data, None, 200
