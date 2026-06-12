import logging
import secrets
import string
from decimal import Decimal
from typing import Optional, Tuple
from urllib.parse import quote, urlparse

from django.conf import settings
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
    coupon_aed_for_points,
    coupon_credit_aed,
    coupon_points_block,
    min_points_to_redeem,
    point_value_aed,
    validate_points_for_coupon,
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
from .services.geidea import GeideaSessionError, create_geidea_session, fetch_geidea_orders_by_merchant_ref
from .services.geidea_callback import process_geidea_callback
from .services.geidea_paybylink import GeideaPayLinkError, create_geidea_payment_link
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
    OrderEditSerializer,
    FCMDeviceTokenSerializer,
    PushSettingsSerializer,
    OrderReturnCreateSerializer,
    OrderReturnReadSerializer,
    order_code_for_order,
    return_flow_ui_payload,
    return_reason_options_payload,
    PurchasePointsLedgerSerializer,
    UserAddressSerializer,
    OfferNotificationSerializer,
    UserNotificationSerializer,
    WishlistItemSerializer,
    WishlistMoveToCartSerializer,
)
from .services.notifications import create_user_notification
from .services.order_email import send_order_placed_email
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


def _resolve_order_pk(request, pk=None):
    """Path ``pk`` or query ``id`` / ``order_id`` (detail, confirm, returns, etc.)."""
    if pk is not None:
        return pk, None
    raw = (request.query_params.get('id') or request.query_params.get('order_id') or '').strip()
    if not raw:
        return None, Response(
            {'detail': 'Query parameter id is required and must be a positive integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not raw.isdigit():
        return None, Response(
            {'detail': 'Query parameter id is required and must be a positive integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return int(raw), None


def _as_decimal(raw, default='0'):
    try:
        return Decimal(str(raw)).quantize(Decimal('0.01'))
    except Exception:
        return Decimal(default).quantize(Decimal('0.01'))


def _zoho_price_from_payload(source: dict, first_variant: dict) -> Decimal:
    """Match Zoho list/detail field names (parent + first variant)."""
    for raw in (
        source.get('min_rate'),
        source.get('rate'),
        source.get('price'),
        source.get('selling_price'),
        source.get('sales_rate'),
        source.get('list_price'),
        source.get('actual_price'),
        source.get('mrp'),
        source.get('label_rate'),
        first_variant.get('rate'),
        first_variant.get('price'),
        first_variant.get('selling_price'),
        first_variant.get('sales_rate'),
        first_variant.get('list_price'),
        first_variant.get('label_rate'),
    ):
        if raw in (None, ''):
            continue
        try:
            val = _as_decimal(raw)
            if val > Decimal('0'):
                return val
        except Exception:
            continue
    variants = source.get('variants') if isinstance(source.get('variants'), list) else []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        for raw in (
            variant.get('rate'),
            variant.get('price'),
            variant.get('selling_price'),
            variant.get('sales_rate'),
            variant.get('list_price'),
            variant.get('label_rate'),
        ):
            if raw in (None, ''):
                continue
            try:
                val = _as_decimal(raw)
                if val > Decimal('0'):
                    return val
            except Exception:
                continue
    return Decimal('0')


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
        or first_variant.get('product_name')
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
    price = _zoho_price_from_payload(source, first_variant)
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
    Fetch one Zoho product row from account/org list, then product detail API.
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
    try:
        return service.get_product_detail(
            organization_id=organization_id,
            product_id=zoho_product_id,
        )
    except Exception:
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
        detail_payload = None
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
            try:
                detail_payload = detail_payload or ZohoCommerceService.get_product_detail_storefront(
                    zoho_product_id,
                    store=store,
                )
                product = _upsert_local_product_from_zoho(store, zoho_product_id, detail_payload)
            except Exception:
                pass
        if not _product_is_valid_for_cart(product, zoho_product_id):
            return (
                None,
                (
                    'Unable to fetch complete product name/price from Zoho for this item. '
                    'Ensure the product has a name and selling price > 0 in Zoho Commerce, '
                    'and use the variant product_id if the item has variants.'
                ),
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


def _checkout_totals(
    subtotal: Decimal,
    vat_percent: Decimal,
    shipping_amount: Decimal,
    *,
    loyalty_discount: Decimal = Decimal('0'),
    coupon_discount: Decimal = Decimal('0'),
    is_free_shipping_coupon: bool = False,  # FIXED: flag for free_shipping coupon type
    is_bxgy_coupon: bool = False,           # FIXED: flag for buyxgety coupon type
    bxgy_get_item_net: Decimal = Decimal('0'),  # FIXED: net cost of the bxgy get-item after its discount
) -> dict[str, Decimal]:
    """VAT applies to subtotal after loyalty and product-level discounts.
    For free_shipping coupons, shipping is zeroed and VAT is on the full product subtotal.
    For buyxgety coupons, the cart subtotal VAT is unaffected; only the get-item is discounted.
    """
    if is_free_shipping_coupon:
        # FIXED: free_shipping — coupon_discount IS the shipping amount being waived.
        # Product subtotal is NOT discounted. VAT is on full product subtotal.
        # Effective shipping = 0 (the whole point of the coupon).
        loyalty_only_discount = loyalty_discount.quantize(Decimal('0.01'))
        taxable_subtotal = max(subtotal - loyalty_only_discount, Decimal('0')).quantize(Decimal('0.01'))
        vat_amount = ((taxable_subtotal * vat_percent) / Decimal('100')).quantize(Decimal('0.01'))
        total = (taxable_subtotal + vat_amount).quantize(Decimal('0.01'))  # FIXED: no shipping added
    elif is_bxgy_coupon:
        # FIXED: buyxgety — the discount only applies to the get-item (Y), not to the cart subtotal.
        # Cart buy-items pay full price + full VAT. Get-item net cost is passed in bxgy_get_item_net.
        loyalty_only_discount = loyalty_discount.quantize(Decimal('0.01'))
        taxable_subtotal = max(subtotal - loyalty_only_discount, Decimal('0')).quantize(Decimal('0.01'))
        vat_amount = ((taxable_subtotal * vat_percent) / Decimal('100')).quantize(Decimal('0.01'))
        # FIXED: total = buy-items + their VAT + get-item net (0 if 100% off) + shipping
        total = (taxable_subtotal + vat_amount + bxgy_get_item_net + shipping_amount).quantize(Decimal('0.01'))
    else:
        # Original logic for transaction / item / loyalty discounts.
        discount_total = (loyalty_discount + coupon_discount).quantize(Decimal('0.01'))
        taxable_subtotal = max(subtotal - discount_total, Decimal('0')).quantize(Decimal('0.01'))
        vat_amount = ((taxable_subtotal * vat_percent) / Decimal('100')).quantize(Decimal('0.01'))
        total = (taxable_subtotal + vat_amount + shipping_amount).quantize(Decimal('0.01'))
    return {
        'taxable_subtotal': taxable_subtotal,
        'vat_amount': vat_amount,
        'total': total,
        'gross_total': total,
    }


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
            shipping_amount = Decimal(ser.validated_data.get('shipping_amount') or Decimal('0')).quantize(
                Decimal('0.01'),
            )
        subtotal = sum((it.line_subtotal for it in items), Decimal('0'))
        subtotal = subtotal.quantize(Decimal('0.01'))
        # VAT percent is always taken from server settings — never trusted from client.
        # Client may still send vat_percent for display purposes but it is ignored here.
        _default_vat = getattr(settings, 'DEFAULT_VAT_PERCENT', '5.00')
        vat_percent = Decimal(str(_default_vat)).quantize(Decimal('0.01'))

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
                # Always recalculate discount server-side — never trust client-provided value.
                offer_coupon_discount_value = calculate_coupon_discount(
                    offer_coupon,
                    cart_snapshots,
                    subtotal,
                    shipping_amount,
                    currency,
                )
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

            loyalty_cap = max(subtotal - offer_coupon_discount_value, Decimal('0')).quantize(Decimal('0.01'))

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
                loyalty_discount = min(Decimal(coupon_row.amount_aed), loyalty_cap).quantize(Decimal('0.01'))

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
                max_pts = max_points_redeemable_for_total(loyalty_cap, pv)
                actual_pts = min(points_to_redeem_req, bal, max_pts)
                if actual_pts <= 0:
                    return Response(
                        {'detail': 'No loyalty points can be applied to this order total.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                discount_calc = (Decimal(actual_pts) * pv).quantize(Decimal('0.01'))
                loyalty_discount = min(discount_calc, loyalty_cap).quantize(Decimal('0.01'))
                loyalty_points_redeemed = actual_pts
                locked_user.points_balance = bal - actual_pts
                locked_user.save(update_fields=['points_balance'])

            checkout_totals = _checkout_totals(
                subtotal,
                vat_percent,
                shipping_amount,
                loyalty_discount=loyalty_discount,
                coupon_discount=offer_coupon_discount_value,
                is_free_shipping_coupon=(  # FIXED: pass flag so shipping is zeroed for free_shipping coupons
                    offer_coupon is not None
                    and (offer_coupon.coupon_type or '').lower() == 'free_shipping'
                ),
            )
            vat_amount = checkout_totals['vat_amount']
            gross_total = checkout_totals['gross_total']
            final_total = checkout_totals['total']

            order = Order.objects.create(
                user=request.user,
                store=store,
                status=Order.Status.PENDING_ZOHO_SYNC,
                tracking_stage_history={},
                currency=currency,
                payment_method=ser.validated_data['payment_method'],
                payment_status=(
                    Order.PaymentStatus.PENDING
                    if ser.validated_data['payment_method'] in (
                        Order.PaymentMethod.PAYMENT_GATEWAY.value,
                        Order.PaymentMethod.PAY_BY_LINK.value,
                        Order.PaymentMethod.CARD_ON_DELIVERY.value,
                    )
                    else Order.PaymentStatus.NOT_REQUIRED
                ),
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

            from shop.services.order_tracking import record_tracking_stage

            record_tracking_stage(order, 'pending', at=order.created_at, save=True)
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
                if bxgy_discount_found:
                    checkout_totals = _checkout_totals(
                        subtotal,
                        vat_percent,
                        shipping_amount,
                        loyalty_discount=loyalty_discount,
                        coupon_discount=offer_coupon_discount_value,
                        is_free_shipping_coupon=False,  # FIXED: buyxgety is never free_shipping
                        is_bxgy_coupon=True,            # FIXED: use bxgy path — don't subtract discount from subtotal
                        bxgy_get_item_net=net_line_total,  # FIXED: net cost of get-item (0 if 100% off)
                    )
                    vat_amount = checkout_totals['vat_amount']
                    gross_total = checkout_totals['gross_total']
                    final_total = checkout_totals['total']
                    order.vat_amount = vat_amount
                    order.total = final_total
                    order.save(update_fields=['vat_amount', 'total', 'updated_at'])
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

            from shop.services.account_credit import credit_user_for_prepaid_order, get_user_credit_balance

            gateway_reference = (ser.validated_data.get('gateway_reference') or '').strip()
            if gateway_reference:
                order.gateway_reference = gateway_reference[:255]
                order.save(update_fields=['gateway_reference', 'updated_at'])

            # payment_gateway: DO NOT credit user at checkout.
            # Credit is applied in the Geidea callback view when Geidea confirms
            # payment (Phase 3). payment_success is always False here for
            # payment_gateway so this block would never execute anyway, but
            # the explicit check makes the intent unambiguous and safe.
            #
            # pay_by_link: unchanged — credit user immediately when
            # payment_success=True is sent at checkout.
            if order.payment_method == Order.PaymentMethod.PAY_BY_LINK:
                if ser.validated_data.get('payment_success'):
                    pay_amount = ser.validated_data.get('payment_amount')
                    if pay_amount is not None and pay_amount != final_total:
                        raise ValidationError({
                            'payment_amount': f'Must match order total ({final_total}).',
                        })
                    credit_user_for_prepaid_order(
                        order,
                        amount=pay_amount if pay_amount is not None else final_total,
                        gateway_reference=gateway_reference,
                    )
                    order.refresh_from_db()

        from shop.services.zoho_books_sales_order import maybe_create_zoho_books_sales_order_for_order
        from shop.services.zoho_sales_order import maybe_create_zoho_sales_order_for_order

        if getattr(settings, 'ZOHO_BOOKS_MANUAL_WORKFLOW', False):
            maybe_create_zoho_books_sales_order_for_order(order.pk, trigger='placed')
            # payment_gateway: advance payment is created by the Geidea callback
            # after Geidea confirms payment. DO NOT create it here.
            #
            # pay_by_link: advance payment is also created by the Geidea callback
            # after the customer pays the link. DO NOT create it here — the customer
            # has not paid yet at checkout time.
            #
            # cash_on_delivery / card_on_delivery: not prepaid, skip advance payment.
            pass
        else:
            maybe_create_zoho_sales_order_for_order(order.pk)

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
        send_order_placed_email(order, request.user)
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
        coupon_discount = offer_coupon_discount_value.quantize(Decimal('0.01'))
        loyalty_discount_amount = order.loyalty_discount.quantize(Decimal('0.01'))
        discount_amount = (coupon_discount + loyalty_discount_amount).quantize(Decimal('0.01'))
        # FIXED: for buyxgety the discount is on the get-item only, not on the cart subtotal.
        # taxable_subtotal must reflect the actual taxable base (cart buy-items minus loyalty only).
        if offer_coupon is not None and (offer_coupon.coupon_type or '').lower() == 'buyxgety':
            taxable_subtotal = max(order.subtotal - loyalty_discount_amount, Decimal('0')).quantize(Decimal('0.01'))  # FIXED
        else:
            taxable_subtotal = max(order.subtotal - discount_amount, Decimal('0')).quantize(Decimal('0.01'))
        order_data = OrderSerializer(order).data
        order_data['coupon_discount'] = str(coupon_discount)
        order_data['discount_amount'] = str(discount_amount)
        order_data['taxable_subtotal'] = str(taxable_subtotal)
        from shop.services.account_credit import get_user_credit_balance

        request.user.refresh_from_db(fields=['credit_balance_aed'])
        response_payload = {
            'order': order_data,
            'credit_balance_aed': str(get_user_credit_balance(request.user)),
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
                    'coupon_discount': str(coupon_discount),
                    'loyalty_discount': str(loyalty_discount_amount),
                    'discount_amount': str(discount_amount),
                    'taxable_subtotal': str(taxable_subtotal),
                    'vat_percent': str(order.vat_percent.quantize(Decimal('0.01'))),
                    'vat_amount': str(order.vat_amount.quantize(Decimal('0.01'))),
                    'shipping_amount': str(order.shipping_amount.quantize(Decimal('0.01'))),
                    'gross_total': str(gross_total.quantize(Decimal('0.01'))),
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
        credit_balance = Decimal(str(getattr(request.user, 'credit_balance_aed', 0) or 0)).quantize(
            Decimal('0.01'),
        )
        return Response(
            {
                # Redeemable balance for checkout / issue-coupon — one wallet for the whole account.
                'wallet_balance': wallet,
                # Backwards compatibility (same value as wallet_balance).
                'points_balance': wallet,
                'credit_balance_aed': str(credit_balance),
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
                    'coupon_points_block': coupon_points_block(),
                    'coupon_credit_aed_per_block': str(coupon_credit_aed()),
                    'can_issue_coupon': wallet >= coupon_points_block(),
                    'max_coupon_blocks_available': wallet // coupon_points_block(),
                    'issue_coupon_path': '/api/shop/rewards/issue-coupon/',
                    'earn_currency': 'AED',
                    'points_balance_is_account_wide': True,
                    'store_fields_are_for_requested_store_only': True,
                },
            },
            status=status.HTTP_200_OK,
        )


class LoyaltyIssueCouponAPIView(APIView):
    """
    Exchange Super Coins for a one-time checkout coupon.

    Default rule: minimum 100 coins, redeemed in blocks of 100 → 100 AED credit each.
    POST body: {} or omit body → one coupon block (100 coins → 100 AED)
               { "points": 200 } → coupon worth 200 AED
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = LoyaltyIssueCouponSerializer(data=request.data or {})
        ser.is_valid(raise_exception=True)
        points = ser.validated_data['points']
        block = coupon_points_block()
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            bal = int(user.points_balance or 0)
            if points > bal:
                return Response({'detail': 'Insufficient points in wallet.'}, status=status.HTTP_400_BAD_REQUEST)
            err = validate_points_for_coupon(points)
            if err:
                return Response({'detail': err}, status=status.HTTP_400_BAD_REQUEST)
            amount_aed = coupon_aed_for_points(points)
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
                'credit_aed': str(coupon.amount_aed),
                'points_spent': coupon.points_spent,
                'points_per_block': block,
                'credit_aed_per_block': str(coupon_credit_aed()),
                'expires_at': coupon.expires_at.isoformat(),
                'message': (
                    f'Coupon created: {coupon.amount_aed} AED store credit. '
                    f'Use code {coupon.code} at checkout.'
                ),
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


class OrderDetailAPIView(APIView):
    """
    GET   /api/shop/orders/detail/?id=<id>&store_id=<store_id>
    PATCH /api/shop/orders/detail/?id=<id>&store_id=<store_id>
    """

    permission_classes = [IsAuthenticated]

    def _order_queryset(self, user, store):
        return (
            Order.objects.filter(user=user, store=store)
            .select_related('store')
            .prefetch_related('items', 'returns__lines__order_item')
        )

    def _resolve_store(self, request):
        raw = (request.query_params.get('store_id') or '').strip()
        if not raw:
            raise ValidationError({'detail': 'store_id query parameter is required.'})
        try:
            store_id = int(raw)
        except (TypeError, ValueError):
            raise ValidationError({'detail': 'store_id must be an integer.'})
        store = Store.objects.filter(pk=store_id, is_active=True).first()
        if not store:
            raise ValidationError({'detail': 'Store not found.'})
        return store

    def get(self, request, pk=None):
        store = self._resolve_store(request)
        pk, err = _resolve_order_pk(request, pk)
        if err:
            return err
        qs = Order.objects.filter(store=store).select_related('store').prefetch_related(
            'items', 'returns__lines__order_item',
        )
        if not (request.user.is_staff or request.user.is_superuser):
            qs = qs.filter(user=request.user)
        order = get_object_or_404(qs, pk=pk)
        return Response(OrderSerializer(order).data)

    def patch(self, request, pk=None):
        store = self._resolve_store(request)
        pk, err = _resolve_order_pk(request, pk)
        if err:
            return err
        qs = Order.objects.select_related('store').prefetch_related('items')
        if request.user.is_staff or request.user.is_superuser:
            order = get_object_or_404(qs, pk=pk, store=store)
        else:
            order = get_object_or_404(qs, pk=pk, store=store, user=request.user)

        if order.status not in ORDER_EDITABLE_STATUSES:
            return Response(
                {'detail': 'Only pending orders can be edited.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ser = OrderEditSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        if not data:
            return Response({'detail': 'No fields to update.'}, status=status.HTTP_400_BAD_REQUEST)

        update_fields: list[str] = ['updated_at']
        simple_fields = (
            'payment_method',
            'shipping_name', 'shipping_phone', 'shipping_address', 'shipping_city',
            'shipping_state', 'shipping_postal_code', 'shipping_country',
            'billing_same_as_shipping',
            'billing_name', 'billing_phone', 'billing_address', 'billing_city',
            'billing_state', 'billing_postal_code', 'billing_country',
        )
        for field in simple_fields:
            if field in data:
                setattr(order, field, data[field])
                update_fields.append(field)

        if data.get('billing_same_as_shipping'):
            order.billing_name = order.shipping_name
            order.billing_phone = order.shipping_phone
            order.billing_address = order.shipping_address
            order.billing_city = order.shipping_city
            order.billing_state = order.shipping_state
            order.billing_postal_code = order.shipping_postal_code
            order.billing_country = order.shipping_country
            update_fields.extend([
                'billing_name', 'billing_phone', 'billing_address', 'billing_city',
                'billing_state', 'billing_postal_code', 'billing_country',
            ])

        if 'shipping_amount' in data:
            shipping_amount = Decimal(data['shipping_amount']).quantize(Decimal('0.01'))
            totals = _checkout_totals(
                Decimal(str(order.subtotal or 0)),
                Decimal(str(order.vat_percent or 0)),
                shipping_amount,
                loyalty_discount=Decimal(str(order.loyalty_discount or 0)),
            )
            order.shipping_amount = shipping_amount
            order.vat_amount = totals['vat_amount']
            order.total = totals['total']
            update_fields.extend(['shipping_amount', 'vat_amount', 'total'])

        order.save(update_fields=list(dict.fromkeys(update_fields)))

        from shop.services.zoho_books_sales_order import maybe_update_zoho_books_sales_order_for_order
        from shop.services.zoho_sales_order import maybe_update_zoho_sales_order_for_order

        if getattr(settings, 'ZOHO_BOOKS_MANUAL_WORKFLOW', False):
            maybe_update_zoho_books_sales_order_for_order(order.pk)
        else:
            maybe_update_zoho_sales_order_for_order(order.pk)

        order = (
            Order.objects.filter(pk=pk, store=store)
            .select_related('store')
            .prefetch_related('items', 'returns__lines__order_item')
            .first()
        )
        return Response(
            {
                'status': 'success',
                'message': 'Order updated.',
                'order': OrderSerializer(order).data,
            },
            status=status.HTTP_200_OK,
        )


ORDER_EDITABLE_STATUSES = frozenset({
    Order.Status.PENDING_ZOHO_SYNC,
    Order.Status.SYNC_FAILED,
})


class OrderConfirmAPIView(APIView):
    """
    Confirm order after staff review: mark synced (local approval).

    POST /api/shop/orders/<pk>/confirm/?store_id=<store_id>
    POST /api/shop/orders/confirm/?order_id=<order_id>&store_id=<store_id>

    With ``ZOHO_BOOKS_MANUAL_WORKFLOW=True`` (default), confirm does not create invoice or
    payment — staff use ``/zoho-books/invoice/`` and ``/zoho-books/payment/`` separately.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {'detail': 'Staff access is required to confirm orders.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        pk, err = _resolve_order_pk(request, pk)
        if err:
            return err

        raw_store_id = (request.query_params.get('store_id') or '').strip()
        if not raw_store_id:
            return Response(
                {'detail': 'store_id query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            store_id = int(raw_store_id)
        except (TypeError, ValueError):
            return Response(
                {'detail': 'store_id must be an integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        store = Store.objects.filter(pk=store_id, is_active=True).first()
        if not store:
            return Response({'detail': 'Store not found.'}, status=status.HTTP_404_NOT_FOUND)

        order = get_object_or_404(
            Order.objects.select_related('store').prefetch_related('items', 'returns__lines__order_item'),
            pk=pk,
            store=store,
        )

        from shop.services.order_sync_state import apply_order_sync_transition
        from shop.services.zoho_books_invoice import (
            maybe_finalize_zoho_books_invoice_for_order,
            zoho_books_manual_workflow,
        )

        already_synced = order.status == Order.Status.SYNCED
        if already_synced:
            if not zoho_books_manual_workflow():
                maybe_finalize_zoho_books_invoice_for_order(order.pk, trigger='synced')
                message = 'Order was already confirmed; retried Zoho Books sync.'
            else:
                message = 'Order was already confirmed.'
        else:
            try:
                apply_order_sync_transition(order, Order.Status.SYNCED)
            except ValueError as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            message = 'Order confirmed.'

        order = (
            Order.objects.filter(pk=order.pk)
            .select_related('store')
            .prefetch_related('items', 'returns__lines__order_item')
            .first()
        )
        return Response(
            {
                'status': 'success',
                'message': message,
                'order': OrderSerializer(order).data,
            },
            status=status.HTTP_200_OK,
        )


def _staff_order_for_books_action(request, pk=None):
    """Resolve store + order for staff-only Zoho Books actions."""
    if not (request.user.is_staff or request.user.is_superuser):
        return None, None, Response(
            {'detail': 'Staff access is required.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    pk, err = _resolve_order_pk(request, pk)
    if err:
        return None, None, err

    raw_store_id = (request.query_params.get('store_id') or '').strip()
    if not raw_store_id:
        return None, None, Response(
            {'detail': 'store_id query parameter is required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        store_id = int(raw_store_id)
    except (TypeError, ValueError):
        return None, None, Response(
            {'detail': 'store_id must be an integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    store = Store.objects.filter(pk=store_id, is_active=True).first()
    if not store:
        return None, None, Response({'detail': 'Store not found.'}, status=status.HTTP_404_NOT_FOUND)

    order = get_object_or_404(
        Order.objects.select_related('store').prefetch_related('items', 'returns__lines__order_item'),
        pk=pk,
        store=store,
    )
    return store, order, None


def _order_for_owner_or_staff_action(request, pk=None):
    """Resolve store + order; staff may act on any order, customers only their own."""
    pk, err = _resolve_order_pk(request, pk)
    if err:
        return None, None, err

    raw_store_id = (request.query_params.get('store_id') or '').strip()
    if not raw_store_id:
        return None, None, Response(
            {'detail': 'store_id query parameter is required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        store_id = int(raw_store_id)
    except (TypeError, ValueError):
        return None, None, Response(
            {'detail': 'store_id must be an integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    store = Store.objects.filter(pk=store_id, is_active=True).first()
    if not store:
        return None, None, Response({'detail': 'Store not found.'}, status=status.HTTP_404_NOT_FOUND)

    qs = Order.objects.select_related('store').prefetch_related('items', 'returns__lines__order_item')
    if not (request.user.is_staff or request.user.is_superuser):
        qs = qs.filter(user=request.user)
    order = get_object_or_404(qs, pk=pk, store=store)
    return store, order, None


class OrderZohoBooksInvoiceAPIView(APIView):
    """
    Staff: create Zoho Books invoice from the order's sales order.

    POST /api/shop/orders/<pk>/zoho-books/invoice/?store_id=<store_id>
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        _, order, err = _staff_order_for_books_action(request, pk)
        if err:
            return err

        if order.payment_method in (
            Order.PaymentMethod.CASH_ON_DELIVERY,
            Order.PaymentMethod.CARD_ON_DELIVERY,
        ):
            method_label = (
                'Card on delivery'
                if order.payment_method == Order.PaymentMethod.CARD_ON_DELIVERY
                else 'Cash on delivery'
            )
            after_step = (
                'Use POST /api/admin/orders/collect-card/?id=<order_id> after Geidea payment.'
                if order.payment_method == Order.PaymentMethod.CARD_ON_DELIVERY
                else 'Use POST /api/admin/orders/{id}/collect-cod/ after delivery.'
            )
            return Response(
                {
                    'status': 'error',
                    'message': (
                        f'{method_label}: confirm the sales order and create the invoice '
                        f'in Zoho Books. {after_step}'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        from shop.services.zoho_books_invoice import staff_create_zoho_books_invoice_for_order

        ok, message = staff_create_zoho_books_invoice_for_order(order.pk)
        order = (
            Order.objects.filter(pk=order.pk)
            .select_related('store')
            .prefetch_related('items', 'returns__lines__order_item')
            .first()
        )
        return Response(
            {
                'status': 'success' if ok else 'error',
                'message': message,
                'order': OrderSerializer(order).data,
            },
            status=status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST,
        )


class OrderZohoBooksPaymentAPIView(APIView):
    """
    Staff: record Zoho Books customer payment against the order invoice.

    POST /api/shop/orders/<pk>/zoho-books/payment/?store_id=<store_id>
    Optional body: ``amount``, ``payment_method``, ``gateway_reference``.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        _, order, err = _staff_order_for_books_action(request, pk)
        if err:
            return err

        if order.payment_method == Order.PaymentMethod.CASH_ON_DELIVERY:
            return Response(
                {
                    'status': 'error',
                    'message': (
                        'Cash on delivery: use POST /api/admin/orders/{id}/collect-cod/ '
                        'after the delivery boy collects cash.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if order.payment_method == Order.PaymentMethod.CARD_ON_DELIVERY:
            return Response(
                {
                    'status': 'error',
                    'message': (
                        'Card on delivery: use POST /api/admin/orders/collect-card/?id=<order_id> '
                        'after Geidea POS payment.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        amount = None
        raw_amount = request.data.get('amount')
        if raw_amount is not None and str(raw_amount).strip() != '':
            amount = Decimal(str(raw_amount)).quantize(Decimal('0.01'))

        payment_method = (request.data.get('payment_method') or '').strip()
        gateway_reference = (request.data.get('gateway_reference') or '').strip()

        from shop.services.zoho_books_payment import staff_record_zoho_books_payment_for_order

        ok, message = staff_record_zoho_books_payment_for_order(
            order.pk,
            amount=amount,
            payment_method=payment_method,
            gateway_reference=gateway_reference,
        )
        steps = [message] if ok else []
        if ok and order.payment_method == Order.PaymentMethod.CARD_ON_DELIVERY:
            from shop.services.order_delivery_payment import maybe_auto_mark_delivered_on_payment

            changed, deliver_msg = maybe_auto_mark_delivered_on_payment(order.pk)
            if changed:
                steps.append(deliver_msg)
        order = (
            Order.objects.filter(pk=order.pk)
            .select_related('store')
            .prefetch_related('items', 'returns__lines__order_item')
            .first()
        )
        return Response(
            {
                'status': 'success' if ok else 'error',
                'message': ' '.join(steps) if steps else message,
                'steps_completed': steps,
                'order': OrderSerializer(order).data,
            },
            status=status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST,
        )


class OrderPaymentSuccessAPIView(APIView):
    """
    Record successful gateway / pay-by-link payment and credit user account.

    POST /api/shop/orders/<pk>/payment-success/?store_id=<store_id>
    Body (optional): ``amount``, ``gateway_reference``

    Callable by the order owner or staff (simulates gateway webhook until integrated).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        _, order, err = _order_for_owner_or_staff_action(request, pk)
        if err:
            return err

        amount = None
        raw_amount = request.data.get('amount')
        if raw_amount is not None and str(raw_amount).strip() != '':
            amount = Decimal(str(raw_amount)).quantize(Decimal('0.01'))

        gateway_reference = (request.data.get('gateway_reference') or '').strip()

        from shop.services.account_credit import get_user_credit_balance, record_prepaid_payment_success

        ok, message, order = record_prepaid_payment_success(
            order.pk,
            amount=amount,
            gateway_reference=gateway_reference,
        )
        if order is None:
            return Response({'status': 'error', 'message': message}, status=status.HTTP_400_BAD_REQUEST)

        order = (
            Order.objects.filter(pk=order.pk)
            .select_related('store', 'user')
            .prefetch_related('items', 'returns__lines__order_item')
            .first()
        )
        return Response(
            {
                'status': 'success' if ok else 'error',
                'message': message,
                'credit_balance_aed': str(get_user_credit_balance(order.user)),
                'order': OrderSerializer(order).data,
            },
            status=status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST,
        )


class OrderZohoBooksCancelAPIView(APIView):
    """
    Staff: void Zoho Books sales order and cancel local order.

    POST /api/shop/orders/<pk>/zoho-books/cancel/?store_id=<store_id>
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        _, order, err = _staff_order_for_books_action(request, pk)
        if err:
            return err

        from shop.services.zoho_books_sales_order import staff_cancel_zoho_books_order

        ok, message = staff_cancel_zoho_books_order(order.pk)
        order = (
            Order.objects.filter(pk=order.pk)
            .select_related('store')
            .prefetch_related('items', 'returns__lines__order_item')
            .first()
        )
        return Response(
            {
                'status': 'success' if ok else 'error',
                'message': message,
                'order': OrderSerializer(order).data,
            },
            status=status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST,
        )


class OrderReturnFlowMetaAPIView(APIView):
    """
    Return-flow metadata: reason codes/labels, cancel vs confirm wiring, and where prices live.

    Item selection: GET order detail → ``return_eligible_lines`` (``unit_price_display`` per line).
    Confirm return: POST ``/api/shop/orders/returns/?order_id=<id>`` (or path ``.../orders/<id>/returns/``).
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
    """
    List or create returns for an order.

    Path: GET/POST /api/shop/orders/<order_id>/returns/
    Query: GET/POST /api/shop/orders/returns/?order_id=<order_id>
    """

    permission_classes = [IsAuthenticated]

    def _resolve_order_pk(self, request, pk=None):
        if pk is not None:
            return pk
        raw_order_id = (request.query_params.get('order_id') or '').strip()
        if not raw_order_id:
            return None
        try:
            return int(raw_order_id)
        except (TypeError, ValueError):
            return None

    def _order_pk_required_response(self):
        return Response(
            {'detail': 'order_id query parameter is required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def _invalid_order_id_response(self):
        return Response(
            {'detail': 'order_id must be an integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def get(self, request, pk=None):
        order_pk = self._resolve_order_pk(request, pk)
        if order_pk is None:
            raw = (request.query_params.get('order_id') or '').strip()
            if raw:
                return self._invalid_order_id_response()
            return self._order_pk_required_response()
        order = get_object_or_404(Order, pk=order_pk, user=request.user)
        qs = (
            order.returns.prefetch_related('lines__order_item')
            .select_related('order')
            .order_by('-created_at')
        )
        return Response(OrderReturnReadSerializer(qs, many=True).data)

    def post(self, request, pk=None):
        order_pk = self._resolve_order_pk(request, pk)
        if order_pk is None:
            raw = (request.query_params.get('order_id') or '').strip()
            if raw:
                return self._invalid_order_id_response()
            return self._order_pk_required_response()
        order = get_object_or_404(Order, pk=order_pk, user=request.user)
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


def _apply_notification_store_filter(request, qs):
    """Optional ``store_id`` query param — filter notifications by ``payload.store_id``."""
    raw = (request.query_params.get('store_id') or '').strip()
    if not raw:
        return qs, None
    try:
        store_id = int(raw)
    except (TypeError, ValueError):
        return None, Response(
            {'detail': 'store_id must be an integer.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    store = Store.objects.filter(pk=store_id, is_active=True).first()
    if not store:
        return None, Response(
            {'detail': 'Store not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    return qs.filter(payload__store_id=store_id), None


def _user_notifications_queryset(user, request, *, offers_only=False):
    if offers_only:
        qs = UserNotification.objects.filter(user=user, kind=UserNotification.Kind.OFFER)
    else:
        qs = UserNotification.objects.filter(user=user).exclude(kind=UserNotification.Kind.OFFER)
    return _apply_notification_store_filter(request, qs)


class NotificationListAPIView(generics.ListAPIView):
    """GET — paginated in-app notifications. PATCH — mark one read via ?id=<pk>&store_id=."""

    permission_classes = [IsAuthenticated]
    serializer_class = UserNotificationSerializer
    pagination_class = NotificationPagination

    def get_queryset(self):
        qs, err = _user_notifications_queryset(self.request.user, self.request)
        if err is not None:
            return UserNotification.objects.none()
        raw = (self.request.query_params.get('unread') or '').strip().lower()
        if raw in ('1', 'true', 'yes'):
            qs = qs.filter(read_at__isnull=True)
        kind = (self.request.query_params.get('kind') or '').strip()
        if kind:
            qs = qs.filter(kind=kind)
        return qs

    def list(self, request, *args, **kwargs):
        _, err = _user_notifications_queryset(request.user, request)
        if err is not None:
            return err
        return super().list(request, *args, **kwargs)

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
        qs, err = _user_notifications_queryset(request.user, request)
        if err is not None:
            return err
        n = get_object_or_404(qs, pk=pk)
        if n.read_at is None:
            n.read_at = timezone.now()
            n.save(update_fields=['read_at'])
        return Response(UserNotificationSerializer(n).data)


class OfferNotificationListAPIView(NotificationListAPIView):
    """GET - paginated offer notifications for the current user."""

    serializer_class = OfferNotificationSerializer

    def get_queryset(self):
        qs = UserNotification.objects.filter(
            user=self.request.user,
            kind=UserNotification.Kind.OFFER,
        )
        raw = (self.request.query_params.get('unread') or '').strip().lower()
        if raw in ('1', 'true', 'yes'):
            qs = qs.filter(read_at__isnull=True)
        org_id = (self.request.query_params.get('org_id') or '').strip()
        if org_id:
            qs = qs.filter(payload__org_id=org_id)
        return qs


class NotificationUnreadCountAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs, err = _apply_notification_store_filter(
            request,
            UserNotification.objects.filter(user=request.user),
        )
        if err is not None:
            return err
        c = qs.filter(read_at__isnull=True).count()
        return Response({'unread_count': c})


class NotificationMarkAllReadAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        now = timezone.now()
        qs, err = _apply_notification_store_filter(
            request,
            UserNotification.objects.filter(user=request.user),
        )
        if err is not None:
            return err
        n = qs.filter(read_at__isnull=True).update(read_at=now)
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


class GeideaInitiateView(APIView):
    """
    POST /api/shop/geidea/initiate/
    Body: { "order_id": <int> }

    Creates a Geidea payment session server-to-server and returns the session_id
    to the frontend. The frontend uses it to launch the Geidea hosted payment page.

    Session expires in 15 minutes — frontend must call startPayment() immediately.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        order_id = request.data.get('order_id')
        if not order_id:
            return Response(
                {'error': 'order_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Fetch the order and verify it belongs to the requesting user.
        # Using user=request.user means even a valid JWT cannot access another
        # user's order — it returns 404 rather than 403 so the order's existence
        # is not revealed.
        try:
            order = Order.objects.get(pk=order_id, user=request.user)
        except Order.DoesNotExist:
            return Response(
                {'error': 'Order not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Guard: only payment_gateway orders go through this flow.
        # cash_on_delivery, card_on_delivery, and pay_by_link must never
        # reach this endpoint.
        if order.payment_method != Order.PaymentMethod.PAYMENT_GATEWAY:
            return Response(
                {'error': 'This order does not use the payment gateway.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Guard: order must still be in a payable state.
        if order.payment_status == Order.PaymentStatus.PAID:
            return Response(
                {'error': 'Order already paid.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Note: PaymentStatus has no CANCELLED constant; cancellation is tracked
        # on Order.Status. Check order.status instead.
        if order.status == Order.Status.CANCELLED:
            return Response(
                {'error': 'Order cancelled.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Guard: Zoho Sales Order must exist.
        # zoho_books_salesorder_id is the merchantReferenceId sent to Geidea.
        # If it is null, the Zoho sync from Phase 1 failed and the frontend
        # should offer a retry button that calls this endpoint again.
        if not order.zoho_books_salesorder_id:
            return Response(
                {'error': 'Order saved, payment cannot start — Zoho sync pending.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create the Geidea session server-to-server.
        try:
            session_id = create_geidea_session(order)
        except GeideaSessionError as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {'session_id': session_id},
            status=status.HTTP_200_OK,
        )


class GeideaCallbackView(APIView):
    """
    POST /api/shop/geidea/callback/
    Open endpoint — no JWT auth. Secured by HMAC signature only.

    Geidea POSTs payment results here after the user completes or
    abandons payment on the HPP. This is the authoritative payment confirmation.

    Always returns HTTP 200 to Geidea except on signature mismatch (400).
    Geidea retries on non-200 — returning 200 on failures prevents retry loops.
    """
    permission_classes = []   # No auth — open endpoint
    authentication_classes = []  # No JWT parsing

    def post(self, request):
        try:
            payload = request.data
        except Exception:
            return Response({"message": "Invalid payload"}, status=400)

        http_status, message = process_geidea_callback(payload)
        return Response({"message": message}, status=http_status)


logger = logging.getLogger(__name__)


class GeideaStatusView(APIView):
    """
    GET /api/shop/geidea/status/?order_id=<pk>

    Manual fallback called by frontend if polling times out.
    Fetches the order status directly from Geidea and reconciles
    if payment succeeded but callback was missed.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        order_id = request.query_params.get('order_id')
        if not order_id:
            return Response(
                {'error': 'order_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            order = Order.objects.get(pk=order_id, user=request.user)
        except Order.DoesNotExist:
            return Response(
                {'error': 'Order not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Already paid — no need to check Geidea
        if order.payment_status == Order.PaymentStatus.PAID:
            return Response({'status': 'paid'})

        if not order.geidea_merchant_ref:
            return Response({'status': 'pending'})

        from shop.services.geidea_reconcile import reconcile_missed_geidea_callback

        reconcile_status, steps = reconcile_missed_geidea_callback(order)
        if reconcile_status == 'paid':
            payload = {'status': 'paid'}
            if steps:
                payload['steps'] = steps
            return Response(payload)

        return Response({'status': 'pending'})


class PayByLinkInitiateView(APIView):
    """
    POST /api/shop/paybylink/initiate/
    Body: { "order_id": <int> }

    Generates a Geidea eInvoice payment link for a pay_by_link order and
    returns the URL to Flutter. Also sends the link to the customer by email.

    Idempotent: calling this endpoint again for an already-linked order returns
    the same URL without hitting the Geidea API again.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        order_id = request.data.get('order_id')
        if not order_id:
            return Response(
                {'detail': 'order_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            order = Order.objects.get(pk=order_id, user=request.user)
        except Order.DoesNotExist:
            return Response(
                {'detail': 'Not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if order.payment_method != Order.PaymentMethod.PAY_BY_LINK:
            return Response(
                {'detail': 'Order is not a pay_by_link order.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if order.status == Order.Status.CANCELLED:
            return Response(
                {'detail': 'Order has been cancelled.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if order.payment_status == Order.PaymentStatus.PAID:
            return Response(
                {'detail': 'Order already paid.', 'order_id': order.pk},
                status=status.HTTP_200_OK,
            )

        # Use select_for_update inside atomic to prevent concurrent requests
        # from creating two payment links for the same order on network timeout
        try:
            with transaction.atomic():
                order = Order.objects.select_for_update().get(pk=order_id, user=request.user)
                # Re-check inside lock — if another request already created the link, return it
                if (order.geidea_paylink_url or '').strip():
                    payment_link = order.geidea_paylink_url
                else:
                    payment_link = create_geidea_payment_link(order)
        except GeideaPayLinkError as exc:
            return Response(
                {'detail': str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Send payment link email — best-effort, never block the response
        try:
            from .services.order_email import send_paybylink_email
            send_paybylink_email(order, request.user)
        except Exception:
            pass

        from datetime import date as _date, timedelta as _timedelta
        expiry_days = getattr(settings, 'GEIDEA_PAYLINK_EXPIRY_DAYS', 7)
        expires_at = (_date.today() + _timedelta(days=expiry_days)).isoformat()

        return Response(
            {
                'payment_link': payment_link,
                'order_id': order.pk,
                'expires_at': expires_at,
            },
            status=status.HTTP_200_OK,
        )
