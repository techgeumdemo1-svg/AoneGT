import secrets
import string
from decimal import Decimal
from typing import Optional, Tuple
from urllib.parse import quote, urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect
from django.utils.text import slugify
from catalog.models import Store, Product
from catalog.services.zoho_product_ids import (
    extract_zoho_category_id_from_detail,
    extract_zoho_collection_id_from_detail,
)
from catalog.text_utils import html_to_plain_text
from zoho_integration.storefront_collections import (
    backfill_product_collection_id_if_empty,
    resolve_zoho_collection_id_via_storefront,
)
from zoho_integration.models import ZohoCommerceAccount
from zoho_integration.services import ZohoCommerceService as ZohoAccountService
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .loyalty import (
    aed_per_point_earned,
    default_coupon_expires_at,
    max_points_redeemable_for_total,
    min_points_to_redeem,
    point_value_aed,
    points_earned_for_purchase,
)
from .models import (
    Cart,
    CartItem,
    FCMDeviceToken,
    LoyaltyIssuedCoupon,
    Order,
    OrderItem,
    OrderReturn,
    PurchasePointsLedger,
    UserAddress,
    UserNotification,
    WishlistItem,
)
from .services.zoho_commerce import ZohoCommerceError, ZohoCommerceService
from offer.models import Coupon
from offer.services import (
    _as_decimal,
    calculate_coupon_discount,
    coupon_is_applicable,
    get_applicable_coupons_for_store,
    get_cart_context,
    get_coupon_for_checkout,
    increment_coupon_usage,
)
from .serializers import (
    CartSerializer,
    CartAddFromZohoAccountSerializer,
    CartItemSerializer,
    CartItemUpdateSerializer,
    CartItemDeltaSerializer,
    CheckoutSerializer,
    LoyaltyIssueCouponSerializer,
    OrderSerializer,
    FCMDeviceTokenSerializer,
    PushSettingsSerializer,
    OrderReturnCreateSerializer,
    OrderReturnReadSerializer,
    order_code_for_order,
    return_flow_ui_payload,
    return_reason_options_payload,
    PurchasePointsLedgerSerializer,
    UserAddressSerializer,
    UserNotificationSerializer,
    WishlistItemSerializer,
    WishlistMoveToCartSerializer,
)
from .services.notifications import create_user_notification
from .services.zoho_returns import enqueue_push_return_to_zoho

User = get_user_model()

WISHLIST_MAX_ITEMS_PER_STORE = 100


def _generate_loyalty_coupon_code() -> str:
    alphabet = string.ascii_uppercase + string.digits

    def chunk(n: int) -> str:
        return ''.join(secrets.choice(alphabet) for _ in range(n))

    return f'{chunk(4)}-{chunk(4)}'


def _optional_store_for_zoho(request):
    """
    Optional ``store_id`` query param selects per-store Zoho storefront domain + org.
    When omitted, global ZOHO_STORE_DOMAIN / ZOHO_ORG_ID are used.
    """
    raw = request.query_params.get('store_id')
    if raw is None or str(raw).strip() == '':
        return None, None
    try:
        pk = int(raw)
    except (TypeError, ValueError):
        return None, Response(
            {'detail': 'store_id must be an integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    store = Store.objects.filter(pk=pk).first()
    if not store:
        return None, Response(
            {'detail': 'Store not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    return store, None


def _required_store_for_user_scope(request):
    raw = (request.query_params.get('store_id') or '').strip()
    if not raw:
        return None, Response(
            {'detail': 'store_id query parameter is required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        pk = int(raw)
    except (TypeError, ValueError):
        return None, Response(
            {'detail': 'store_id must be an integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    store = Store.objects.filter(pk=pk, is_active=True).first()
    if not store:
        return None, Response(
            {'detail': 'Store not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    return store, None


def _as_decimal(raw, default='0'):
    try:
        return Decimal(str(raw)).quantize(Decimal('0.01'))
    except Exception:
        return Decimal(default).quantize(Decimal('0.01'))


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
    fallback_name = f'Zoho Product {zoho_product_id}'
    slug_source = name
    if (slug_source or '').strip() == fallback_name:
        # Prefer another meaningful identifier before falling back to raw id.
        slug_source = (
            source.get('product_name')
            or source.get('item_name')
            or source.get('seo_keyword')
            or source.get('sku')
            or first_variant.get('sku')
            or zoho_product_id
        )
    base_slug = slugify(str(slug_source or '').strip()) or f'product-{zoho_product_id}'
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

    resolved_name = name
    if (
        product.pk
        and ((name or '').strip() == fallback_name)
        and (product.name or '').strip()
        and (product.name or '').strip() != fallback_name
    ):
        # Do not overwrite an existing real name with fallback.
        resolved_name = product.name

    resolved_sku = sku[:120] if sku else (product.sku or '')
    resolved_category = category[:255] if category else (product.category or '')
    resolved_description = description if description else (product.description or '')
    resolved_currency = currency[:8] if currency else (product.currency or 'AED')
    resolved_image_url = image_url[:500] if image_url else (product.image_url or '')

    z_cat = (extract_zoho_category_id_from_detail(payload) or '').strip()[:120]
    z_col = (extract_zoho_collection_id_from_detail(payload) or '').strip()[:120]
    if not z_col:
        z_col = (resolve_zoho_collection_id_via_storefront(store, zoho_product_id) or '').strip()[:120]
    resolved_zoho_category_id = (
        z_cat if z_cat else ((product.zoho_category_id or '')[:120] if product.pk else '')
    )
    resolved_zoho_collection_id = (
        z_col if z_col else ((product.zoho_collection_id or '')[:120] if product.pk else '')
    )

    # Keep existing non-zero price when payload only has fallback 0.
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

    # If product was created earlier with a technical slug, upgrade it once
    # a meaningful name becomes available.
    if product.pk and (product.slug or '').startswith('zoho-product-'):
        desired_base_slug = slugify(resolved_name) or base_slug
        desired_slug = desired_base_slug[:255]
        suffix = 1
        while Product.objects.filter(store=store, slug=desired_slug).exclude(pk=product.pk).exists():
            suffix += 1
            desired_slug = f'{desired_base_slug[:245]}-{suffix}'[:255]
        product.slug = desired_slug

    product.name = resolved_name[:255]
    product.sku = resolved_sku
    product.category = resolved_category
    product.description = resolved_description
    product.price = resolved_price
    product.compare_at_price = resolved_compare_at_price
    product.currency = resolved_currency
    product.image_url = resolved_image_url
    product.zoho_category_id = resolved_zoho_category_id
    product.zoho_collection_id = resolved_zoho_collection_id
    product.is_active = True
    product.save()
    return product


def _extract_image_url_from_zoho_payload(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ''
    product_blob = payload.get('product') if isinstance(payload, dict) else None
    source = product_blob if isinstance(product_blob, dict) else payload
    if not isinstance(source, dict):
        return ''
    variants = source.get('variants') if isinstance(source.get('variants'), list) else []
    first_variant = variants[0] if variants and isinstance(variants[0], dict) else {}
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
    return str(
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


def _build_zoho_cdn_image_url(store_domain: str, payload: dict) -> str:
    domain = _normalize_zoho_store_domain(store_domain)
    if not domain or not isinstance(payload, dict):
        return ''
    source = payload.get('product') if isinstance(payload.get('product'), dict) else payload
    if not isinstance(source, dict):
        return ''

    top_document_id = str(
        source.get('document_id')
        or source.get('image_document_id')
        or source.get('image_id')
        or ''
    ).strip()
    if top_document_id:
        return (
            f'https://cdn1.zohoecommerce.com/category-images/{quote(top_document_id)}/800x800'
            f'?storefront_domain={domain}'
        )

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


def _resolve_cdn_image_for_store_product(store: Store, zoho_product_id: str) -> str:
    org_id = str(getattr(store, 'zoho_org_id', '') or '').strip()
    account = None
    if org_id:
        account = ZohoCommerceAccount.objects.filter(
            is_active=True,
            organization_id=org_id,
        ).first()
    if account is None:
        client_id = str(getattr(store, 'client_id', '') or '').strip()
        refresh_token = str(getattr(store, 'refresh_token', '') or '').strip()
        if client_id:
            account = ZohoCommerceAccount.objects.filter(
                is_active=True,
                client_id=client_id,
            ).first()
        if account is None and refresh_token:
            account = ZohoCommerceAccount.objects.filter(
                is_active=True,
                refresh_token=refresh_token,
            ).first()
    if account is None:
        account = ZohoCommerceAccount.objects.filter(is_active=True).first()
    if account is None:
        return ''
    org_for_request = org_id or str(getattr(account, 'organization_id', '') or '').strip()
    if not org_for_request:
        return ''
    try:
        detail = ZohoAccountService(account).get_product_detail(
            organization_id=org_for_request,
            product_id=str(zoho_product_id),
        )
    except Exception:
        return ''
    return _build_zoho_cdn_image_url(
        str(getattr(store, 'zoho_store_domain', '') or ''),
        detail,
    )


def _resolve_or_create_store_for_zoho_account(
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


def _fetch_zoho_product_from_account(
    account: ZohoCommerceAccount,
    organization_id: str,
    zoho_product_id: str,
):
    """
    Fetch one Zoho product row from account/org product list response.
    """
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


def _perform_cart_add_zoho_product(
    user,
    store: Store,
    zoho_product_id: str,
    quantity: int,
    *,
    account: Optional[ZohoCommerceAccount] = None,
    organization_id: Optional[str] = None,
):
    """Returns (response_data|None, error_detail|None, http_status)."""
    def _product_is_valid_for_cart(p: Product, pid: str) -> bool:
        fallback_name = f'Zoho Product {pid}'
        name_ok = bool((p.name or '').strip()) and (p.name or '').strip() != fallback_name
        try:
            price_ok = Decimal(str(p.price or '0')) > Decimal('0')
        except Exception:
            price_ok = False
        return name_ok and price_ok

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
        # Keep local row up-to-date from Zoho list payload on every add.
        product = _upsert_local_product_from_zoho(store, zoho_product_id, fresh_zoho_payload)
    elif product is not None and not (product.sku or '').strip():
        # Backfill legacy rows that were created before SKU fallback existed.
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
            return None, str(e), status.HTTP_502_BAD_GATEWAY
    elif not (product.image_url or '').strip():
        # If list payload doesn't include image URL, enrich from detail payload.
        try:
            detail_payload = ZohoCommerceService.get_product_detail_storefront(
                zoho_product_id,
                store=store,
            )
            product = _upsert_local_product_from_zoho(store, zoho_product_id, detail_payload)
        except ZohoCommerceError:
            pass

    # Enforce a valid product snapshot for cart responses:
    # - non-fallback name
    # - price greater than zero
    # When account/org is present, retry once with account-level detail endpoint.
    if product is not None and not _product_is_valid_for_cart(product, zoho_product_id):
        if account is not None and organization_id:
            try:
                detail_payload = ZohoAccountService(account).get_product_detail(
                    organization_id=organization_id,
                    product_id=zoho_product_id,
                )
                product = _upsert_local_product_from_zoho(store, zoho_product_id, detail_payload)
            except Exception:
                pass
        if not _product_is_valid_for_cart(product, zoho_product_id):
            return (
                None,
                'Unable to fetch complete product name/price from Zoho for this item.',
                status.HTTP_502_BAD_GATEWAY,
            )

    if product is not None:
        backfill_product_collection_id_if_empty(store, product, zoho_product_id)

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
    return CartItemSerializer(item).data, None, status.HTTP_200_OK


class CartDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store, err = _required_store_for_user_scope(request)
        if err:
            return err
        cart, _ = Cart.objects.get_or_create(user=request.user)
        cart = (
            Cart.objects.filter(pk=cart.pk)
            .prefetch_related('items__product', 'items__store')
            .first()
        )
        payload = CartSerializer(cart).data if cart else {
            'cart_id': None, 'items': [], 'subtotal': '0.00', 'updated_at': None,
        }
        if isinstance(payload, dict):
            items = payload.get('items') or []
            if isinstance(items, list):
                payload['items'] = [
                    row for row in items
                    if isinstance(row, dict)
                    and isinstance(row.get('store'), dict)
                    and row['store'].get('id') == store.pk
                ]
            subtotal = Decimal('0')
            for row in payload.get('items') or []:
                try:
                    subtotal += Decimal(str(row.get('line_subtotal') or '0'))
                except Exception:
                    pass
            payload['subtotal'] = str(subtotal.quantize(Decimal('0.01')))
        store_cache = {}
        product_cache = {}
        cdn_cache = {}

        def _get_store(store_id):
            key = int(store_id) if store_id is not None else None
            if key is None:
                return None
            if key not in store_cache:
                store_cache[key] = Store.objects.filter(pk=key).first()
            return store_cache[key]

        def _get_product(product_id):
            key = int(product_id) if product_id is not None else None
            if key is None:
                return None
            if key not in product_cache:
                product_cache[key] = Product.objects.filter(pk=key).first()
            return product_cache[key]

        def _resolve_for_product(store_obj, product_obj):
            if store_obj is None or product_obj is None:
                return ''
            zoho_pid = str(getattr(product_obj, 'zoho_product_id', '') or '').strip()
            if not zoho_pid:
                return ''
            cache_key = (store_obj.pk, zoho_pid)
            if cache_key not in cdn_cache:
                cdn_cache[cache_key] = _resolve_cdn_image_for_store_product(store_obj, zoho_pid)
            return cdn_cache[cache_key]

        item_rows = payload.get('items') if isinstance(payload, dict) else []
        if isinstance(item_rows, list):
            for row in item_rows:
                if not isinstance(row, dict):
                    continue
                store_obj = row.get('store') or {}
                product_obj = row.get('product') or {}
                if not isinstance(store_obj, dict) or not isinstance(product_obj, dict):
                    continue
                current_image = str(product_obj.get('image_url') or '').strip()
                if (
                    current_image
                    and '/api/shop/zoho-products/' not in current_image
                ):
                    continue
                store = _get_store(store_obj.get('id'))
                product_model = _get_product(product_obj.get('id'))
                cdn_url = _resolve_for_product(store, product_model)
                if cdn_url:
                    product_obj['image_url'] = cdn_url
                    row['product'] = product_obj

        store_groups = payload.get('store_groups') if isinstance(payload, dict) else []
        if isinstance(store_groups, list):
            for grp in store_groups:
                if not isinstance(grp, dict):
                    continue
                store_obj = grp.get('store') or {}
                if not isinstance(store_obj, dict):
                    continue
                store = _get_store(store_obj.get('id'))
                if store is None:
                    continue
                grp_items = grp.get('items') or []
                if not isinstance(grp_items, list):
                    continue
                for line in grp_items:
                    if not isinstance(line, dict):
                        continue
                    product_obj = line.get('product') or {}
                    if not isinstance(product_obj, dict):
                        continue
                    current_image = str(product_obj.get('image_url') or '').strip()
                    if (
                        current_image
                        and '/api/shop/zoho-products/' not in current_image
                    ):
                        continue
                    product_model = _get_product(product_obj.get('id'))
                    cdn_url = _resolve_for_product(store, product_model)
                    if cdn_url:
                        product_obj['image_url'] = cdn_url
                        line['product'] = product_obj

        return Response(payload, status=status.HTTP_200_OK)


class CartSummaryAPIView(APIView):
    """
    Lightweight cart footer/badge summary.
    - products_count: number of distinct lines in cart
    - items_count: sum of quantities (e.g., 4+2+4 = 10)
    - subtotal: total price
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        store, err = _required_store_for_user_scope(request)
        if err:
            return err
        cart, _ = Cart.objects.get_or_create(user=request.user)
        cart = (
            Cart.objects.filter(pk=cart.pk)
            .prefetch_related('items__product')
            .first()
        )
        items = list(cart.items.filter(store_id=store.pk)) if cart else []
        items_count = int(sum((int(i.quantity or 0) for i in items), 0))
        products_count = int(len(items))
        subtotal = sum((i.line_subtotal for i in items), Decimal('0')).quantize(Decimal('0.01'))
        return Response(
            {
                'cart_id': cart.pk if cart else None,
                'products_count': products_count,
                'items_count': items_count,
                'subtotal': str(subtotal),
            },
            status=status.HTTP_200_OK,
        )


class CartClearAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        store, err = _required_store_for_user_scope(request)
        if err:
            return err
        cart, _ = Cart.objects.get_or_create(user=request.user)
        deleted_count, _details = cart.items.filter(store_id=store.pk).delete()
        return Response(
            {
                'status': 'success',
                'message': 'Cart cleared successfully.',
                'deleted_items': int(deleted_count),
            },
            status=status.HTTP_200_OK,
        )


class UserAddressListCreateAPIView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserAddressSerializer

    def get_queryset(self):
        return UserAddress.objects.filter(user=self.request.user).order_by(
            '-is_default', '-updated_at', '-created_at',
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class UserAddressDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserAddressSerializer

    def get_queryset(self):
        return UserAddress.objects.filter(user=self.request.user)


class CartAddItemAPIView(APIView):
    """
    Add to cart using the same ids as /zoho/multi/stores/ and
    /zoho/multi/accounts/<account_id>/products/<organization_id>/.

    Body: zoho_account_id, organization_id, zoho_product_id, quantity,
    optional primary_domain (from store list for this org — required if no local Store yet).
    """

    permission_classes = [IsAuthenticated]

    def _get_item_from_query(self, request):
        store, err = _required_store_for_user_scope(request)
        if err:
            return None, err
        item_id_raw = (request.query_params.get('item_id') or '').strip()
        if not item_id_raw:
            return None, Response(
                {'detail': 'item_id query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            item_id = int(item_id_raw)
        except ValueError:
            return None, Response(
                {'detail': 'item_id must be an integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        item = CartItem.objects.filter(
            pk=item_id,
            cart__user=request.user,
            store=store,
        ).select_related('product', 'store').first()
        if item is None:
            return None, Response(
                {'detail': 'Cart item not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return item, None

    def get(self, request):
        item, err = self._get_item_from_query(request)
        if err:
            return err
        return Response(CartItemSerializer(item, context={'request': request}).data, status=status.HTTP_200_OK)

    def patch(self, request):
        item, err = self._get_item_from_query(request)
        if err:
            return err
        if 'action' in request.data:
            delta_ser = CartItemDeltaSerializer(data=request.data)
            delta_ser.is_valid(raise_exception=True)
            action = delta_ser.validated_data['action']
            step = delta_ser.validated_data['step']
            if action == 'increment':
                item.quantity = int(item.quantity) + int(step)
            else:
                item.quantity = max(1, int(item.quantity) - int(step))
            item.save(update_fields=['quantity'])
        else:
            ser = CartItemUpdateSerializer(item, data=request.data, partial=True)
            ser.is_valid(raise_exception=True)
            ser.save()
        item.refresh_from_db()
        item = CartItem.objects.select_related('product', 'store').get(pk=item.pk)
        return Response(CartItemSerializer(item, context={'request': request}).data, status=status.HTTP_200_OK)

    def delete(self, request):
        item, err = self._get_item_from_query(request)
        if err:
            return err
        deleted_item_id = item.pk
        item.delete()
        return Response(
            {'status': 'success', 'message': 'Cart item removed.', 'item_id': deleted_item_id},
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        ser = CartAddFromZohoAccountSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        account = get_object_or_404(
            ZohoCommerceAccount.objects.filter(is_active=True),
            pk=ser.validated_data['zoho_account_id'],
        )
        organization_id = ser.validated_data['organization_id']
        zoho_product_id = ser.validated_data['zoho_product_id']
        quantity = ser.validated_data['quantity']
        primary_domain = ser.validated_data.get('primary_domain') or ''

        store, resolve_err = _resolve_or_create_store_for_zoho_account(
            account,
            organization_id,
            primary_domain,
        )
        if resolve_err:
            return Response({'detail': resolve_err}, status=status.HTTP_400_BAD_REQUEST)

        existing_qs = WishlistItem.objects.filter(user=request.user, store=store)
        existing_count = existing_qs.count()
        existing_item = existing_qs.filter(product__zoho_product_id=zoho_product_id).first()
        if existing_count >= WISHLIST_MAX_ITEMS_PER_STORE and not existing_item:
            return Response(
                {
                    'detail': (
                        f'Wishlist limit reached for this store. '
                        f'Maximum {WISHLIST_MAX_ITEMS_PER_STORE} items allowed.'
                    ),
                    'max_items': WISHLIST_MAX_ITEMS_PER_STORE,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        data, err, st = _perform_cart_add_zoho_product(
            request.user,
            store,
            zoho_product_id,
            quantity,
            account=account,
            organization_id=organization_id,
        )
        if err:
            return Response({'detail': err}, status=st)
        result = dict(data)
        result['zoho_product_id'] = str(zoho_product_id)
        product_info = result.get('product') or {}
        if isinstance(product_info, dict):
            result['category_id'] = (product_info.get('category_id') or '').strip()
            result['collection_id'] = (product_info.get('collection_id') or '').strip()
            current_image = (product_info.get('image_url') or '').strip()
            if (
                not current_image
                or current_image.startswith('/api/shop/zoho-products/')
                or '/api/shop/zoho-products/' in current_image
            ):
                resolved_cdn = ''
                try:
                    detail = ZohoAccountService(account).get_product_detail(
                        organization_id=organization_id,
                        product_id=str(zoho_product_id),
                    )
                    resolved_cdn = _build_zoho_cdn_image_url(
                        str(getattr(store, 'zoho_store_domain', '') or primary_domain or ''),
                        detail,
                    )
                except Exception:
                    resolved_cdn = ''

                if resolved_cdn:
                    product_info['image_url'] = resolved_cdn
                else:
                    proxy_url = request.build_absolute_uri(
                        f"/api/shop/zoho-products/{zoho_product_id}/image/?store_id={store.pk}"
                    )
                    product_info['image_url'] = proxy_url
                result['product'] = product_info
            result['product_name'] = product_info.get('name', '')
            result['sku'] = product_info.get('sku', '')
            result['unit_price'] = product_info.get('price', '0.00')
        result['local_store_id'] = store.pk
        result['line_total'] = result.get('line_subtotal', '0.00')
        result['total_amount'] = result.get('line_subtotal', '0.00')
        return Response(result, status=st)


class WishlistListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store, err = _required_store_for_user_scope(request)
        if err:
            return err
        qs = WishlistItem.objects.filter(user=request.user, store=store).select_related('product', 'store')
        return Response(WishlistItemSerializer(qs, many=True, context={'request': request}).data)

    def post(self, request):
        ser = CartAddFromZohoAccountSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        account = get_object_or_404(
            ZohoCommerceAccount.objects.filter(is_active=True),
            pk=ser.validated_data['zoho_account_id'],
        )
        organization_id = ser.validated_data['organization_id']
        zoho_product_id = ser.validated_data['zoho_product_id']
        primary_domain = ser.validated_data.get('primary_domain') or ''

        store, resolve_err = _resolve_or_create_store_for_zoho_account(
            account,
            organization_id,
            primary_domain,
        )
        if resolve_err:
            return Response({'detail': resolve_err}, status=status.HTTP_400_BAD_REQUEST)

        product = Product.objects.filter(
            is_active=True,
            store=store,
            zoho_product_id=zoho_product_id,
        ).first()
        # Use account-level detail endpoint as source-of-truth.
        # If this fails, do not silently save fallback values.
        try:
            account_service = ZohoAccountService(account)
            zoho_payload = account_service.get_product_detail(
                organization_id=organization_id,
                product_id=zoho_product_id,
            )
        except Exception as e:
            return Response(
                {'detail': f'Unable to fetch full product detail from Zoho: {e}'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        try:
            product = _upsert_local_product_from_zoho(store, zoho_product_id, zoho_payload)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_502_BAD_GATEWAY)

        item, created = WishlistItem.objects.get_or_create(
            user=request.user,
            store=store,
            product=product,
        )

        payload = WishlistItemSerializer(item, context={'request': request}).data
        payload['already_exists'] = not created
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class WishlistItemDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_item_from_query(self, request):
        raw = (request.query_params.get('wishlist_item_id') or '').strip()
        if not raw:
            return None, Response(
                {'detail': 'wishlist_item_id query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            wid = int(raw)
        except ValueError:
            return None, Response(
                {'detail': 'wishlist_item_id must be an integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        store, err = _required_store_for_user_scope(request)
        if err:
            return None, err
        item = WishlistItem.objects.filter(
            pk=wid,
            user=request.user,
            store=store,
        ).select_related('product', 'store').first()
        if item is None:
            return None, Response(
                {'detail': 'Wishlist item not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return item, None

    def get(self, request):
        item, err = self._get_item_from_query(request)
        if err:
            return err
        return Response(
            WishlistItemSerializer(item, context={'request': request}).data,
            status=status.HTTP_200_OK,
        )

    def delete(self, request):
        item, err = self._get_item_from_query(request)
        if err:
            return err
        deleted_id = item.pk
        item.delete()
        return Response(
            {
                'status': 'success',
                'message': 'Wishlist item removed.',
                'wishlist_item_id': deleted_id,
            },
            status=status.HTTP_200_OK,
        )


class WishlistMoveToCartAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        store, err = _required_store_for_user_scope(request)
        if err:
            return err
        raw = (request.query_params.get('wishlist_item_id') or '').strip()
        if not raw:
            return Response(
                {'detail': 'wishlist_item_id query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            wid = int(raw)
        except ValueError:
            return Response(
                {'detail': 'wishlist_item_id must be an integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        item = get_object_or_404(
            WishlistItem.objects.select_related('product', 'store'),
            pk=wid,
            user=request.user,
            store=store,
        )
        ser = WishlistMoveToCartSerializer(data=request.data or {})
        ser.is_valid(raise_exception=True)

        quantity = ser.validated_data['quantity']
        remove_from_wishlist = ser.validated_data['remove_from_wishlist']

        with transaction.atomic():
            cart, _ = Cart.objects.select_for_update().get_or_create(user=request.user)
            cart_item, created = CartItem.objects.select_for_update().get_or_create(
                cart=cart,
                product=item.product,
                defaults={'quantity': quantity, 'store': item.store},
            )
            if not created:
                cart_item.quantity += quantity
                cart_item.store = item.store
                cart_item.save(update_fields=['quantity', 'store'])

            if remove_from_wishlist:
                item.delete()

        cart_item = CartItem.objects.select_related('product', 'store').get(pk=cart_item.pk)
        response_data = {
            'status': 'success',
            'message': 'Wishlist item moved to cart.',
            'removed_from_wishlist': remove_from_wishlist,
            'cart_item': CartItemSerializer(cart_item, context={'request': request}).data,
        }
        return Response(response_data, status=status.HTTP_200_OK)


class CheckoutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = CheckoutSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        cart = ser.validated_data['cart']
        store = ser.validated_data['store']
        items = list(ser.validated_data['checkout_items'])
        points_to_redeem_req = int(ser.validated_data.get('points_to_redeem') or 0)
        coupon_code_in = (ser.validated_data.get('loyalty_coupon_code') or '').strip()
        offer_coupon_code = (ser.validated_data.get('coupon_code') or '').strip()
        offer_coupon_discount = ser.validated_data.get('coupon_discount')

        if getattr(settings, 'CHECKOUT_TRUST_CLIENT_SHIPPING', False):
            shipping_amount = ser.validated_data.get('shipping_amount') or Decimal('0')
            shipping_amount = Decimal(shipping_amount).quantize(Decimal('0.01'))
        else:
            shipping_amount = Decimal(settings.DEFAULT_SHIPPING_AMOUNT).quantize(Decimal('0.01'))
        subtotal = sum((it.line_subtotal for it in items), Decimal('0'))
        subtotal = subtotal.quantize(Decimal('0.01'))
        vat_percent = Decimal(ser.validated_data.get('vat_percent') or '0').quantize(Decimal('0.01'))
        vat_amount = ((subtotal * vat_percent) / Decimal('100')).quantize(Decimal('0.01'))
        gross_total = (subtotal + vat_amount + shipping_amount).quantize(Decimal('0.01'))

        billing_same = ser.validated_data['billing_same_as_shipping']
        ship = {k: ser.validated_data[k] for k in (
            'shipping_name', 'shipping_phone', 'shipping_address', 'shipping_city',
            'shipping_state', 'shipping_postal_code', 'shipping_country',
        )}
        if billing_same:
            bill = {
                'billing_name': ship['shipping_name'],
                'billing_phone': ship['shipping_phone'],
                'billing_address': ship['shipping_address'],
                'billing_city': ship['shipping_city'],
                'billing_state': ship['shipping_state'],
                'billing_postal_code': ship['shipping_postal_code'],
                'billing_country': ship['shipping_country'],
            }
        else:
            bill = {k: ser.validated_data[k] for k in (
                'billing_name', 'billing_phone', 'billing_address', 'billing_city',
                'billing_state', 'billing_postal_code', 'billing_country',
            )}

        currency = items[0].product.currency if items else 'AED'
        pv = point_value_aed()
        loyalty_discount = Decimal('0')
        loyalty_points_redeemed = 0
        coupon_row = None
        points_awarded = 0
        offer_coupon = None
        live_redemption = 0
        offer_coupon_discount_value = Decimal('0')

        with transaction.atomic():
            locked_user = User.objects.select_for_update().get(pk=request.user.pk)

            if coupon_code_in:
                coupon_row = (
                    LoyaltyIssuedCoupon.objects.select_for_update()
                    .filter(
                        user_id=locked_user.pk,
                        code__iexact=coupon_code_in,
                        used_at__isnull=True,
                    )
                    .first()
                )
                if not coupon_row:
                    return Response(
                        {'detail': 'Invalid or already used loyalty coupon code.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if coupon_row.expires_at < timezone.now():
                    return Response(
                        {'detail': 'This loyalty coupon has expired.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                loyalty_discount = min(Decimal(coupon_row.amount_aed), gross_total).quantize(Decimal('0.01'))

            elif points_to_redeem_req > 0:
                bal = int(locked_user.points_balance or 0)
                min_w = min_points_to_redeem()
                if bal < min_w:
                    return Response(
                        {
                            'detail': (
                                f'You need at least {min_w} points in your wallet before redeeming.'
                            ),
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                max_pts = max_points_redeemable_for_total(gross_total, pv)
                actual_pts = min(points_to_redeem_req, bal, max_pts)
                if actual_pts <= 0:
                    return Response(
                        {'detail': 'No loyalty points can be applied to this order total.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                discount_calc = (Decimal(actual_pts) * pv).quantize(Decimal('0.01'))
                loyalty_discount = min(discount_calc, gross_total).quantize(Decimal('0.01'))
                loyalty_points_redeemed = actual_pts
                locked_user.points_balance = bal - actual_pts
                locked_user.save(update_fields=['points_balance'])

            if offer_coupon_code:
                offer_coupon = get_coupon_for_checkout(store, offer_coupon_code)
                if offer_coupon is None:
                    return Response({'error': 'Coupon not found'}, status=status.HTTP_400_BAD_REQUEST)
                local_redemption = int(offer_coupon.redemption_count or 0)
                local_max = int(offer_coupon.max_redemption_count or 0)
                if local_max > 0 and local_redemption >= local_max:
                    return Response(
                        {'error': 'Sorry, this coupon is no longer available. Please place your order without it.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                cart_snapshots = [
                    {
                        'product_id': str(getattr(it.product, 'zoho_product_id', '') or ''),
                        'category_id': str(getattr(it.product, 'zoho_category_id', '') or ''),
                        'collection_id': str(getattr(it.product, 'zoho_collection_id', '') or ''),
                        'quantity': int(it.quantity or 0),
                        'line_total': _as_decimal(it.line_subtotal),
                    }
                    for it in items
                ]
                allowed, reason = coupon_is_applicable(offer_coupon, locked_user, cart_snapshots, subtotal)
                if not allowed:
                    return Response({'error': reason}, status=status.HTTP_400_BAD_REQUEST)
                if offer_coupon_discount is not None:
                    offer_coupon_discount_value = Decimal(str(offer_coupon_discount)).quantize(Decimal('0.01'))
            else:
                applicable = get_applicable_coupons_for_store(locked_user, store)
                auto_coupons = applicable.get('auto_applied_coupons') or []
                first_auto = auto_coupons[0] if isinstance(auto_coupons, list) and auto_coupons else None
                if isinstance(first_auto, dict):
                    auto_coupon_id = str(first_auto.get('coupon_id') or '').strip()
                    if auto_coupon_id:
                        org_raw = (getattr(store, 'zoho_org_id', '') or getattr(settings, 'ZOHO_COMMERCE_ORGANIZATION_ID', '')).strip()
                        try:
                            org_id = int(org_raw)
                        except Exception:
                            org_id = None
                        coupon_qs = Coupon.objects.filter(coupon_id=auto_coupon_id)
                        if org_id is not None:
                            coupon_qs = coupon_qs.filter(org_id=org_id)
                        offer_coupon = coupon_qs.first()

                if offer_coupon is not None:
                    cart_snapshots = [
                        {
                            'product_id': str(getattr(it.product, 'zoho_product_id', '') or ''),
                            'category_id': str(getattr(it.product, 'zoho_category_id', '') or ''),
                            'collection_id': str(getattr(it.product, 'zoho_collection_id', '') or ''),
                            'quantity': int(it.quantity or 0),
                            'line_total': _as_decimal(it.line_subtotal),
                        }
                        for it in items
                    ]
                    allowed, _reason = coupon_is_applicable(offer_coupon, locked_user, cart_snapshots, subtotal)
                    if allowed:
                        offer_coupon_discount_value = calculate_coupon_discount(
                            offer_coupon,
                            cart_snapshots,
                            subtotal,
                            shipping_amount,
                            currency,
                        )
                    else:
                        offer_coupon = None

            final_total = (gross_total - loyalty_discount).quantize(Decimal('0.01'))
            if offer_coupon is not None:
                final_total = (final_total - offer_coupon_discount_value).quantize(Decimal('0.01'))
            if final_total < 0:
                final_total = Decimal('0')

            order = Order.objects.create(
                user=request.user,
                store=store,
                status=Order.Status.PENDING_ZOHO_SYNC,
                currency=currency,
                payment_method=ser.validated_data['payment_method'],
                subtotal=subtotal,
                vat_percent=vat_percent,
                vat_amount=vat_amount,
                shipping_amount=shipping_amount,
                total=final_total,
                loyalty_points_redeemed=loyalty_points_redeemed,
                loyalty_discount=loyalty_discount,
                billing_same_as_shipping=billing_same,
                **ship,
                **bill,
            )
            for it in items:
                p = it.product
                line = it.line_subtotal.quantize(Decimal('0.01'))
                OrderItem.objects.create(
                    order=order,
                    product=p,
                    product_name=p.name,
                    sku=p.sku,
                    unit_price=p.price,
                    quantity=it.quantity,
                    line_total=line,
                )
            if offer_coupon is not None and (offer_coupon.coupon_type or '').lower() == 'buyxgety':
                get_products = offer_coupon.get_products if isinstance(offer_coupon.get_products, dict) else {}
                get_product_rows = get_products.get('products', []) if isinstance(get_products, dict) else []
                get_qty = float(get_products.get('quantity') or 1) if isinstance(get_products, dict) else 1.0
                max_count = float(offer_coupon.max_discounted_product_count_per_cart or get_qty)
                bxgy_discount_total = Decimal('0.00')
                bxgy_discount_found = False
                for product_row in get_product_rows if isinstance(get_product_rows, list) else []:
                    if not isinstance(product_row, dict):
                        continue
                    zoho_product_id = str(product_row.get('product_id') or '').strip()
                    if not zoho_product_id:
                        continue
                    get_product = Product.objects.filter(
                        store=store,
                        zoho_product_id=zoho_product_id,
                    ).first()
                    if get_product is None:
                        continue
                    get_unit_price = get_product.price
                    get_quantity = int(max_count)
                    get_line_total = (get_unit_price * Decimal(str(get_quantity))).quantize(Decimal('0.01'))
                    discount_pct = _as_decimal(offer_coupon.discount_value or '0')
                    get_item_discount = (get_line_total * discount_pct / Decimal('100')).quantize(Decimal('0.01'))
                    net_line_total = (get_line_total - get_item_discount).quantize(Decimal('0.01'))
                    OrderItem.objects.create(
                        order=order,
                        product=get_product,
                        product_name=get_product.name,
                        sku=get_product.sku,
                        unit_price=get_unit_price,
                        quantity=get_quantity,
                        line_total=net_line_total,
                    )
                    bxgy_discount_total += get_item_discount
                    bxgy_discount_found = True
                    break
                offer_coupon_discount_value = bxgy_discount_total
            CartItem.objects.filter(pk__in=[i.pk for i in items]).delete()

            if offer_coupon is not None:
                try:
                    increment_coupon_usage(
                        offer_coupon,
                        order_id=order.pk,
                        user_id=request.user.pk,
                        discount_amount=offer_coupon_discount_value,
                    )
                except Exception:
                    pass

            if coupon_row:
                coupon_row.used_at = timezone.now()
                coupon_row.order = order
                coupon_row.save(update_fields=['used_at', 'order'])

            points_awarded = points_earned_for_purchase(final_total, currency)
            if points_awarded > 0:
                step = aed_per_point_earned()
                PurchasePointsLedger.objects.create(
                    user=request.user,
                    order=order,
                    points_awarded=points_awarded,
                    note=(
                        f'Earned {points_awarded} pt(s): 1 pt per {step} AED of paid total '
                        f'(after loyalty discount).'
                    ),
                )
                uearn = User.objects.select_for_update().get(pk=request.user.pk)
                uearn.points_balance = int(uearn.points_balance or 0) + points_awarded
                uearn.save(update_fields=['points_balance'])

        order = Order.objects.prefetch_related(
            'items', 'returns__lines__order_item',
        ).get(pk=order.pk)
        code = order_code_for_order(order)
        create_user_notification(
            request.user,
            UserNotification.Kind.ORDER,
            title=f'Order #{code} placed',
            body=(
                f'We received your order ({order.currency} {order.total}). '
                'We will update you when it ships.'
            ),
            payload={
                'event': 'order_placed',
                'order_id': order.pk,
                'store_id': order.store_id,
                'order_code': code,
            },
        )
        if points_awarded > 0:
            create_user_notification(
                request.user,
                UserNotification.Kind.POINTS_REWARD,
                title=f'You earned {points_awarded} points',
                body='Points were added to your wallet from this purchase.',
                payload={
                    'event': 'points_earned',
                    'points': points_awarded,
                    'order_id': order.pk,
                },
            )
        if loyalty_points_redeemed > 0:
            create_user_notification(
                request.user,
                UserNotification.Kind.POINTS_DEDUCTED,
                title=f'{loyalty_points_redeemed} points applied',
                body='Loyalty points were redeemed on this order.',
                payload={
                    'event': 'points_redeemed_checkout',
                    'points': loyalty_points_redeemed,
                    'order_id': order.pk,
                },
            )
        selected_payment_method = {
            'code': order.payment_method,
            'label': Order.PaymentMethod(order.payment_method).label,
            'selected': True,
        }
        order_lines = [
            {
                'name': item.product_name,
                'quantity': item.quantity,
                'line_total': str(item.line_total.quantize(Decimal('0.01'))),
            }
            for item in order.items.all()
        ]
        response_payload = {
            'order': OrderSerializer(order).data,
            'checkout_view': {
                'delivery_address': {
                    'name': order.shipping_name,
                    'phone': order.shipping_phone,
                    'address_line': order.shipping_address,
                    'city': order.shipping_city,
                    'state': order.shipping_state,
                    'country': order.shipping_country,
                },
                'payment_methods': [selected_payment_method],
                'order_summary': {
                    'items': order_lines,
                    'subtotal': str(order.subtotal.quantize(Decimal('0.01'))),
                    'vat_percent': str(order.vat_percent.quantize(Decimal('0.01'))),
                    'vat_amount': str(order.vat_amount.quantize(Decimal('0.01'))),
                    'shipping_amount': str(order.shipping_amount.quantize(Decimal('0.01'))),
                    'gross_total': str(gross_total.quantize(Decimal('0.01'))),
                    'loyalty_discount': str(order.loyalty_discount.quantize(Decimal('0.01'))),
                    'points_redeemed': order.loyalty_points_redeemed,
                    'points_earned': points_awarded,
                    'total': str(order.total.quantize(Decimal('0.01'))),
                    'currency': order.currency,
                },
            },
        }
        return Response(response_payload, status=status.HTTP_201_CREATED)


class RewardPointsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store, err = _required_store_for_user_scope(request)
        if err:
            return err
        qs = PurchasePointsLedger.objects.filter(
            user=request.user,
            order__store=store,
        ).select_related('order')
        ledger_sum = int(sum((int(e.points_awarded or 0) for e in qs), 0))
        entries = qs[:20]
        ledger_awarded_all_stores = (
            PurchasePointsLedger.objects.filter(user=request.user).aggregate(s=Sum('points_awarded'))[
                's'
            ]
            or 0
        )
        ledger_awarded_all_stores = int(ledger_awarded_all_stores)
        request.user.refresh_from_db(fields=['points_balance'])
        wallet = int(request.user.points_balance or 0)
        return Response(
            {
                # Redeemable balance for checkout / issue-coupon — one wallet for the whole account.
                'wallet_balance': wallet,
                # Backwards compatibility (same value as wallet_balance).
                'points_balance': wallet,
                'wallet_scope': 'account_wide',
                'store_id': store.pk,
                # Sum of ledger rows for orders placed at this store only (subset of lifetime earn).
                'store_points_earned_from_orders': ledger_sum,
                # Sum of all earn entries in PurchasePointsLedger (all stores); wallet is lower if points were redeemed.
                'ledger_points_awarded_total_all_stores': ledger_awarded_all_stores,
                'history': PurchasePointsLedgerSerializer(entries, many=True).data,
                'loyalty': {
                    'aed_spend_per_point_earned': aed_per_point_earned(),
                    'point_value_aed': str(point_value_aed()),
                    'min_points_to_redeem': min_points_to_redeem(),
                    'earn_currency': 'AED',
                    'points_balance_is_account_wide': True,
                    'store_fields_are_for_requested_store_only': True,
                },
            },
            status=status.HTTP_200_OK,
        )


class LoyaltyIssueCouponAPIView(APIView):
    """Exchange wallet points for a one-time code usable at checkout (same discount rules)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = LoyaltyIssueCouponSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        points = ser.validated_data['points']
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            bal = int(user.points_balance or 0)
            if points > bal:
                return Response({'detail': 'Insufficient points in wallet.'}, status=status.HTTP_400_BAD_REQUEST)
            amount_aed = (Decimal(points) * point_value_aed()).quantize(Decimal('0.01'))
            user.points_balance = bal - points
            user.save(update_fields=['points_balance'])
            code = _generate_loyalty_coupon_code()
            tries = 0
            while LoyaltyIssuedCoupon.objects.filter(code__iexact=code).exists():
                code = _generate_loyalty_coupon_code()
                tries += 1
                if tries > 12:
                    raise RuntimeError('Could not allocate unique loyalty coupon code.')
            coupon = LoyaltyIssuedCoupon.objects.create(
                user=user,
                code=code,
                points_spent=points,
                amount_aed=amount_aed,
                expires_at=default_coupon_expires_at(),
            )
        create_user_notification(
            user,
            UserNotification.Kind.POINTS_DEDUCTED,
            title=f'{points} points exchanged',
            body=(
                f'Store credit coupon {coupon.code} '
                f'({coupon.amount_aed} AED) is ready to use at checkout.'
            ),
            payload={
                'event': 'coupon_issued',
                'points': points,
                'coupon_code': coupon.code,
                'amount_aed': str(coupon.amount_aed),
            },
        )
        return Response(
            {
                'code': coupon.code,
                'amount_aed': str(coupon.amount_aed),
                'points_spent': coupon.points_spent,
                'expires_at': coupon.expires_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class OrderListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OrderSerializer

    def get_queryset(self):
        raw = (self.request.query_params.get('store_id') or '').strip()
        if not raw:
            raise ValidationError({'detail': 'store_id query parameter is required.'})
        try:
            store_id = int(raw)
        except (TypeError, ValueError):
            raise ValidationError({'detail': 'store_id must be an integer.'})
        store = Store.objects.filter(pk=store_id, is_active=True).first()
        if not store:
            raise ValidationError({'detail': 'Store not found.'})
        return (
            Order.objects.filter(user=self.request.user, store=store)
            .select_related('store')
            .prefetch_related('items', 'returns__lines__order_item')
        )


class OrderDetailAPIView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OrderSerializer

    def get_queryset(self):
        raw = (self.request.query_params.get('store_id') or '').strip()
        if not raw:
            raise ValidationError({'detail': 'store_id query parameter is required.'})
        try:
            store_id = int(raw)
        except (TypeError, ValueError):
            raise ValidationError({'detail': 'store_id must be an integer.'})
        store = Store.objects.filter(pk=store_id, is_active=True).first()
        if not store:
            raise ValidationError({'detail': 'Store not found.'})
        return (
            Order.objects.filter(user=self.request.user, store=store)
            .select_related('store')
            .prefetch_related('items', 'returns__lines__order_item')
        )


class OrderReturnFlowMetaAPIView(APIView):
    """
    Return-flow metadata: reason codes/labels, cancel vs confirm wiring, and where prices live.

    Item selection: GET order detail → ``return_eligible_lines`` (``unit_price_display`` per line).
    Confirm return: POST ``/api/shop/orders/<order_id>/returns/`` after reason step.
    Cancel: client-only (close modal); no server endpoint.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                'return_reasons': return_reason_options_payload(),
                'return_flow': return_flow_ui_payload(),
            },
            status=status.HTTP_200_OK,
        )


class OrderReturnListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        order = get_object_or_404(Order, pk=pk, user=request.user)
        qs = (
            order.returns.prefetch_related('lines__order_item')
            .select_related('order')
            .order_by('-created_at')
        )
        return Response(OrderReturnReadSerializer(qs, many=True).data)

    def post(self, request, pk):
        order = get_object_or_404(Order, pk=pk, user=request.user)
        ser = OrderReturnCreateSerializer(
            data=request.data,
            context={'order': order, 'request': request},
        )
        ser.is_valid(raise_exception=True)
        ret = ser.save()
        enqueue_push_return_to_zoho(ret.pk)
        ret = (
            OrderReturn.objects.select_related('order')
            .prefetch_related('lines__order_item')
            .get(pk=ret.pk)
        )
        create_user_notification(
            request.user,
            UserNotification.Kind.ORDER,
            title='Return request submitted',
            body='We are processing your return.',
            payload={
                'event': 'return_submitted',
                'return_id': ret.pk,
                'order_id': order.pk,
                'order_code': order_code_for_order(order),
            },
        )
        return Response(OrderReturnReadSerializer(ret).data, status=status.HTTP_201_CREATED)


class OrderReorderAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        order_id = pk
        if order_id is None:
            raw_order_id = (request.query_params.get('order_id') or '').strip()
            if not raw_order_id:
                return Response(
                    {'detail': "order_id query param is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                order_id = int(raw_order_id)
            except (TypeError, ValueError):
                return Response(
                    {'detail': "order_id must be an integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        order = get_object_or_404(Order, pk=order_id, user=request.user)
        mode = (request.query_params.get('mode') or 'merge').strip().lower()
        if mode not in {'merge', 'replace'}:
            return Response(
                {'detail': "Invalid mode. Use 'merge' or 'replace'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        with transaction.atomic():
            cart, _ = Cart.objects.select_for_update().get_or_create(
                user=request.user,
            )
            if mode == 'replace':
                cart.items.all().delete()
            for oi in order.items.select_related('product'):
                p = oi.product
                if not p or not p.is_active:
                    continue
                st = p.store
                item, created = CartItem.objects.select_for_update().get_or_create(
                    cart=cart,
                    product=p,
                    defaults={'quantity': oi.quantity, 'store': st},
                )
                if not created:
                    item.quantity += oi.quantity
                    item.store = st
                    item.save(update_fields=['quantity', 'store'])
        return Response(
            {
                'detail': 'Items added to your cart.',
                'mode': mode,
                'store_id': order.store_id,
            },
            status=status.HTTP_200_OK,
        )


class ZohoProductListAPIView(APIView):
    """
    GET — Zoho Commerce storefront product list (proxied JSON for the app).

    Query: ``store_id`` (optional, local Store pk — uses that store's zoho_store_domain / zoho_org_id),
    ``page``, ``per_page``, ``product_type`` (optional).
    When ``store_id`` is omitted, uses ZOHO_STORE_DOMAIN / ZOHO_ORG_ID from settings.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        store, err = _optional_store_for_zoho(request)
        if err:
            return err
        try:
            page = int(request.query_params.get('page', 1))
            per_page = int(request.query_params.get('per_page', 20))
        except (TypeError, ValueError):
            return Response(
                {'detail': 'page and per_page must be integers.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if page < 1:
            return Response({'detail': 'page must be >= 1.'}, status=status.HTTP_400_BAD_REQUEST)
        if per_page < 1 or per_page > 200:
            return Response(
                {'detail': 'per_page must be between 1 and 200.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        product_type = request.query_params.get('product_type') or None
        if product_type is not None:
            product_type = product_type.strip() or None

        try:
            data = ZohoCommerceService.get_products_storefront(
                product_type=product_type,
                page=page,
                per_page=per_page,
                store=store,
            )
            return Response(data, status=status.HTTP_200_OK)
        except ZohoCommerceError as e:
            msg = str(e)
            st = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if ('Set ZOHO' in msg or 'required' in msg.lower())
                else status.HTTP_502_BAD_GATEWAY
            )
            return Response({'detail': msg}, status=st)


class ZohoProductDetailAPIView(APIView):
    """
    GET — Zoho Commerce storefront product detail by Zoho product_id.

    Query: ``store_id`` (optional) — same as list endpoint.
    """

    permission_classes = [AllowAny]

    @staticmethod
    def _with_plain_description(data):
        if not isinstance(data, dict):
            return data
        payload = data.copy()
        source = payload.get('product') if isinstance(payload.get('product'), dict) else payload
        clean = html_to_plain_text(source.get('description'))
        if clean:
            source = source.copy()
            source['description'] = clean
            if isinstance(payload.get('product'), dict):
                payload['product'] = source
            else:
                payload['description'] = clean
        return payload

    def get(self, request, product_id):
        store, err = _optional_store_for_zoho(request)
        if err:
            return err
        try:
            data = ZohoCommerceService.get_product_detail_storefront(
                product_id, store=store,
            )
            return Response(self._with_plain_description(data), status=status.HTTP_200_OK)
        except ZohoCommerceError as e:
            msg = str(e)
            if 'required' in msg.lower():
                return Response({'detail': msg}, status=status.HTTP_400_BAD_REQUEST)
            st = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if ('Set ZOHO' in msg or 'domain' in msg.lower())
                else status.HTTP_502_BAD_GATEWAY
            )
            return Response({'detail': msg}, status=st)


class ZohoProductImageProxyAPIView(APIView):
    """
    GET — resolves and redirects to a product image URL when available.
    Query: ``store_id`` (optional) — same as list/detail endpoints.
    """

    permission_classes = [AllowAny]

    @staticmethod
    def _is_usable_image_url(value: str) -> bool:
        raw = (value or '').strip()
        return raw.startswith('http://') or raw.startswith('https://')

    def get(self, request, product_id):
        store, err = _optional_store_for_zoho(request)
        if err:
            return err
        try:
            image_url = ''

            # 1) Storefront detail (public) first.
            try:
                data = ZohoCommerceService.get_product_detail_storefront(
                    product_id,
                    store=store,
                )
                image_url = _extract_image_url_from_zoho_payload(data)
            except ZohoCommerceError:
                image_url = ''
            if self._is_usable_image_url(image_url):
                return redirect(image_url)

            # 2) Local catalog fallback if image was previously saved.
            if store is not None:
                local_image = (
                    Product.objects.filter(store=store, zoho_product_id=str(product_id))
                    .values_list('image_url', flat=True)
                    .first()
                ) or ''
                if self._is_usable_image_url(local_image):
                    return redirect(local_image)

                # 3) Account-level detail fallback (OAuth store API).
                org_id = str(getattr(store, 'zoho_org_id', '') or '').strip()
                if org_id:
                    account = ZohoCommerceAccount.objects.filter(
                        is_active=True,
                        organization_id=org_id,
                    ).first()
                    if account is not None:
                        try:
                            detail = ZohoAccountService(account).get_product_detail(
                                organization_id=org_id,
                                product_id=str(product_id),
                            )
                            source = (
                                detail.get('product')
                                or detail.get('item')
                                or detail.get('data')
                                or detail
                            )
                            if isinstance(source, dict):
                                image_url = _extract_image_url_from_zoho_payload(source)
                                if self._is_usable_image_url(image_url):
                                    return redirect(image_url)
                        except Exception:
                            pass

            placeholder_url = str(getattr(settings, 'ZOHO_IMAGE_PLACEHOLDER_URL', '') or '').strip()
            if self._is_usable_image_url(placeholder_url):
                return redirect(placeholder_url)

            return Response(
                {
                    'detail': 'No direct image URL found in Zoho payload for this product.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        except ZohoCommerceError as e:
            msg = str(e)
            st = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if ('Set ZOHO' in msg or 'domain' in msg.lower())
                else status.HTTP_502_BAD_GATEWAY
            )
            return Response({'detail': msg}, status=st)


class RegisterDeviceView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = FCMDeviceTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data['token']
        device_type = serializer.validated_data['device_type']

        FCMDeviceToken.objects.update_or_create(
            token=token,
            defaults={
                'user': request.user,
                'device_type': device_type,
                'is_active': True,
            },
        )
        return Response(
            {
                'status': 'registered',
                'token': token,
                'device_type': device_type,
            },
            status=status.HTTP_200_OK,
        )


class UnregisterDeviceView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        token = str(request.data.get('token') or '').strip()
        if not token:
            return Response({'token': ['Token is required.']}, status=status.HTTP_400_BAD_REQUEST)

        FCMDeviceToken.objects.filter(user=request.user, token=token).update(is_active=False)
        return Response({'status': 'unregistered'}, status=status.HTTP_200_OK)


class PushSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        row = (
            FCMDeviceToken.objects.filter(user=request.user, is_active=True)
            .order_by('-updated_at')
            .first()
        )
        push_enabled = row.push_enabled if row is not None else True
        return Response({'push_enabled': push_enabled}, status=status.HTTP_200_OK)

    def patch(self, request):
        serializer = PushSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        push_enabled = serializer.validated_data['push_enabled']
        FCMDeviceToken.objects.filter(user=request.user, is_active=True).update(
            push_enabled=push_enabled,
        )
        return Response({'push_enabled': push_enabled}, status=status.HTTP_200_OK)


class NotificationPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 50


class NotificationListAPIView(generics.ListAPIView):
    """GET — paginated in-app notifications. PATCH — mark one read via ?id=<pk>."""

    permission_classes = [IsAuthenticated]
    serializer_class = UserNotificationSerializer
    pagination_class = NotificationPagination

    def get_queryset(self):
        qs = UserNotification.objects.filter(user=self.request.user)
        raw = (self.request.query_params.get('unread') or '').strip().lower()
        if raw in ('1', 'true', 'yes'):
            qs = qs.filter(read_at__isnull=True)
        kind = (self.request.query_params.get('kind') or '').strip()
        if kind:
            qs = qs.filter(kind=kind)
        return qs

    def patch(self, request):
        raw_id = (request.query_params.get('id') or '').strip()
        if not raw_id:
            return Response(
                {'detail': 'Query parameter "id" is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            pk = int(raw_id)
        except ValueError:
            return Response({'detail': 'Invalid id.'}, status=status.HTTP_400_BAD_REQUEST)
        n = get_object_or_404(UserNotification, pk=pk, user=request.user)
        if n.read_at is None:
            n.read_at = timezone.now()
            n.save(update_fields=['read_at'])
        return Response(UserNotificationSerializer(n).data)


class NotificationUnreadCountAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        c = UserNotification.objects.filter(user=request.user, read_at__isnull=True).count()
        return Response({'unread_count': c})


class NotificationMarkAllReadAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        now = timezone.now()
        n = UserNotification.objects.filter(user=request.user, read_at__isnull=True).update(
            read_at=now,
        )
        return Response({'marked': n})

class NotificationDetailAPIView(APIView):
    """PATCH — mark one notification read (idempotent)."""

    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        n = get_object_or_404(UserNotification, pk=pk, user=request.user)
        if n.read_at is None:
            n.read_at = timezone.now()
            n.save(update_fields=['read_at'])
        return Response(UserNotificationSerializer(n).data)