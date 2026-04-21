from decimal import Decimal
from typing import Optional, Tuple
from urllib.parse import urlparse

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.utils.text import slugify
from catalog.models import Store, Product
from zoho_integration.models import ZohoCommerceAccount
from zoho_integration.services import ZohoCommerceService as ZohoAccountService
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Cart, CartItem, Order, OrderItem, OrderReturn
from .services.zoho_commerce import ZohoCommerceError, ZohoCommerceService
from .serializers import (
    CartSerializer,
    CartAddFromZohoAccountSerializer,
    CartItemSerializer,
    CartItemUpdateSerializer,
    CheckoutSerializer,
    OrderSerializer,
    OrderReturnCreateSerializer,
    OrderReturnReadSerializer,
)
from .services.zoho_returns import enqueue_push_return_to_zoho


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
        # Do not overwrite an existing real name with fallback.
        resolved_name = product.name

    resolved_sku = sku[:120] if sku else (product.sku or '')
    resolved_category = category[:255] if category else (product.category or '')
    resolved_description = description if description else (product.description or '')
    resolved_currency = currency[:8] if currency else (product.currency or 'AED')
    resolved_image_url = image_url[:500] if image_url else (product.image_url or '')

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
        cart, _ = Cart.objects.get_or_create(user=request.user)
        cart = (
            Cart.objects.filter(pk=cart.pk)
            .prefetch_related('items__product', 'items__store')
            .first()
        )
        return Response(CartSerializer(cart).data, status=status.HTTP_200_OK)


class CartAddItemAPIView(APIView):
    """
    Add to cart using the same ids as /zoho/multi/stores/ and
    /zoho/multi/accounts/<account_id>/products/<organization_id>/.

    Body: zoho_account_id, organization_id, zoho_product_id, quantity,
    optional primary_domain (from store list for this org — required if no local Store yet).
    """

    permission_classes = [IsAuthenticated]

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
        product_info = result.get('product') or {}
        if isinstance(product_info, dict):
            if not (product_info.get('image_url') or '').strip():
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


class CartItemDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CartItemSerializer

    def get_queryset(self):
        return CartItem.objects.filter(cart__user=self.request.user).select_related(
            'product', 'store',
        )

    def get_serializer_class(self):
        if self.request.method in ('PATCH', 'PUT'):
            return CartItemUpdateSerializer
        return CartItemSerializer

    def perform_destroy(self, instance):
        super().perform_destroy(instance)


class CheckoutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = CheckoutSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        cart = ser.validated_data['cart']
        store = ser.validated_data['store']
        items = list(ser.validated_data['checkout_items'])
        if getattr(settings, 'CHECKOUT_TRUST_CLIENT_SHIPPING', False):
            shipping_amount = ser.validated_data.get('shipping_amount') or Decimal('0')
            shipping_amount = Decimal(shipping_amount).quantize(Decimal('0.01'))
        else:
            shipping_amount = Decimal(settings.DEFAULT_SHIPPING_AMOUNT).quantize(Decimal('0.01'))
        subtotal = sum((it.line_subtotal for it in items), Decimal('0'))
        subtotal = subtotal.quantize(Decimal('0.01'))
        total = (subtotal + shipping_amount).quantize(Decimal('0.01'))

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

        with transaction.atomic():
            order = Order.objects.create(
                user=request.user,
                store=store,
                status=Order.Status.PENDING_ZOHO_SYNC,
                currency=currency,
                subtotal=subtotal,
                shipping_amount=shipping_amount,
                total=total,
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
            CartItem.objects.filter(pk__in=[i.pk for i in items]).delete()

        order = Order.objects.prefetch_related(
            'items', 'returns__lines__order_item',
        ).get(pk=order.pk)
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)


class OrderListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OrderSerializer

    def get_queryset(self):
        return (
            Order.objects.filter(user=self.request.user)
            .select_related('store')
            .prefetch_related('items', 'returns__lines__order_item')
        )


class OrderDetailAPIView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OrderSerializer

    def get_queryset(self):
        return (
            Order.objects.filter(user=self.request.user)
            .select_related('store')
            .prefetch_related('items', 'returns__lines__order_item')
        )


class OrderReturnListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        order = get_object_or_404(Order, pk=pk, user=request.user)
        qs = order.returns.prefetch_related('lines').order_by('-created_at')
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
        ret = OrderReturn.objects.prefetch_related('lines').get(pk=ret.pk)
        return Response(OrderReturnReadSerializer(ret).data, status=status.HTTP_201_CREATED)


class OrderReorderAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        order = get_object_or_404(Order, pk=pk, user=request.user)
        with transaction.atomic():
            cart, _ = Cart.objects.select_for_update().get_or_create(
                user=request.user,
            )
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
            {'detail': 'Items merged into your cart.', 'store_id': order.store_id},
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

    def get(self, request, product_id):
        store, err = _optional_store_for_zoho(request)
        if err:
            return err
        try:
            data = ZohoCommerceService.get_product_detail_storefront(
                product_id, store=store,
            )
            return Response(data, status=status.HTTP_200_OK)
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

    def get(self, request, product_id):
        store, err = _optional_store_for_zoho(request)
        if err:
            return err
        try:
            data = ZohoCommerceService.get_product_detail_storefront(
                product_id,
                store=store,
            )
            image_url = _extract_image_url_from_zoho_payload(data)
            if not image_url:
                return Response(
                    {'detail': 'No direct image URL found in Zoho payload for this product.'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return redirect(image_url)
        except ZohoCommerceError as e:
            msg = str(e)
            st = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if ('Set ZOHO' in msg or 'domain' in msg.lower())
                else status.HTTP_502_BAD_GATEWAY
            )
            return Response({'detail': msg}, status=st)