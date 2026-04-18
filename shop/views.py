from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from catalog.models import Store, Product
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Cart, CartItem, Order, OrderItem, OrderReturn
from .services.zoho_commerce import ZohoCommerceError, ZohoCommerceService
from .serializers import (
    CartSerializer,
    CartAddZohoItemSerializer,
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

    name = str(
        source.get('name')
        or source.get('product_name')
        or source.get('item_name')
        or f'Zoho Product {zoho_product_id}'
    ).strip()
    sku = str(source.get('sku') or '').strip()
    category = str(source.get('category_name') or source.get('category') or '').strip()
    description = str(source.get('description') or '').strip()
    currency = str(source.get('currency_code') or source.get('currency') or 'AED').strip() or 'AED'
    price = _as_decimal(source.get('rate') or source.get('price') or source.get('selling_price') or '0')
    compare_at_price_raw = source.get('regular_price') or source.get('compare_at_price')
    compare_at_price = (
        _as_decimal(compare_at_price_raw)
        if compare_at_price_raw not in (None, '')
        else None
    )
    image_url = str(
        source.get('image_url')
        or source.get('image_name')
        or source.get('image')
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

    product.name = name[:255]
    product.sku = sku[:120]
    product.category = category[:255]
    product.description = description
    product.price = price
    product.compare_at_price = compare_at_price
    product.currency = currency[:8]
    product.image_url = image_url[:500]
    product.is_active = True
    product.save()
    return product


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
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = CartAddZohoItemSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        store = ser.validated_data['store']
        zoho_product_id = ser.validated_data['zoho_product_id']
        quantity = ser.validated_data['quantity']
        product = Product.objects.filter(
            is_active=True,
            store=store,
            zoho_product_id=zoho_product_id,
        ).first()
        if product is None:
            try:
                zoho_payload = ZohoCommerceService.get_product_detail_storefront(
                    zoho_product_id,
                    store=store,
                )
                product = _upsert_local_product_from_zoho(store, zoho_product_id, zoho_payload)
            except ZohoCommerceError as e:
                return Response({'detail': str(e)}, status=status.HTTP_502_BAD_GATEWAY)

        with transaction.atomic():
            cart, _ = Cart.objects.select_for_update().get_or_create(
                user=request.user,
            )
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
        return Response(CartItemSerializer(item).data, status=status.HTTP_200_OK)


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