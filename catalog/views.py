from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Banner, Store, Product
from .services.zoho_commerce_products import (
    ZohoCommerceProductError,
    build_product_editpage_url,
    build_products_list_url,
    zoho_commerce_proxy_get,
)
from .services.zoho_sites import (
    fetch_zoho_shop_products,
    fetch_zoho_shops_from_accounts,
)
from shop.services.zoho_commerce import ZohoCommerceError
from .serializers import (
    StoreListSerializer,
    ProductListSerializer,
    ProductDetailSerializer,
    StoreAdminSerializer,
    ProductAdminSerializer,
    BannerSerializer,
    BannerAdminSerializer,
)


def _optional_store_for_zoho_proxy(request):
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


class ProductPageNumberPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class BannerListAPIView(generics.ListAPIView):
    """
    GET — active banners for carousel.

    Query: store_id (optional). When set, returns banners with no store (global)
    plus banners for that store.
    """

    permission_classes = [AllowAny]
    serializer_class = BannerSerializer

    def get_queryset(self):
        qs = Banner.objects.filter(is_active=True).select_related('store').order_by('sort_order', 'id')
        raw = self.request.query_params.get('store_id')
        if raw is None or str(raw).strip() == '':
            return qs
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            return Banner.objects.none()
        return qs.filter(Q(store_id__isnull=True) | Q(store_id=sid))


class BannerAdminListCreateAPIView(generics.ListCreateAPIView):
    """Staff only (JWT + is_staff). GET all banners; POST add."""

    permission_classes = [IsAdminUser]
    queryset = Banner.objects.select_related('store').order_by('sort_order', 'id')
    serializer_class = BannerAdminSerializer


class BannerAdminDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    """Staff only. GET/PATCH/DELETE one banner."""

    permission_classes = [IsAdminUser]
    queryset = Banner.objects.select_related('store').all()
    serializer_class = BannerAdminSerializer


class StoreListAPIView(generics.ListAPIView):
    """
    GET — list all active stores (your 9 storefronts).
    """
    serializer_class = StoreListSerializer
    queryset = Store.objects.filter(is_active=True)


class StoreProductListAPIView(generics.ListAPIView):
    """
    GET — paginated products for one store.
    Query: search (name/sku), page, page_size
    """
    serializer_class = ProductListSerializer
    pagination_class = ProductPageNumberPagination

    def get_queryset(self):
        store = get_object_or_404(Store, pk=self.kwargs['store_id'], is_active=True)
        qs = Product.objects.filter(store=store, is_active=True).order_by('name')
        q = (self.request.query_params.get('search') or '').strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q))
        return qs


class ZohoCommerceShopListAPIView(APIView):
    """
    GET — list shops from Zoho Commerce sites index in a mobile-friendly shape.

    Query:
    - account_id=<zoho account pk> (optional): fetch shops for one configured
      ZohoCommerceAccount; omitted means all active accounts.
    """

    def get(self, request):
        raw_account_id = (request.query_params.get('account_id') or '').strip()
        account_id = None
        if raw_account_id:
            try:
                account_id = int(raw_account_id)
            except (TypeError, ValueError):
                return Response(
                    {'status': 'error', 'message': 'account_id must be an integer.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        try:
            data = fetch_zoho_shops_from_accounts(account_id=account_id)
        except ZohoCommerceError as e:
            return Response(
                {'status': 'error', 'message': str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                'status': 'success',
                'message': 'Stores fetched successfully',
                'mode': 'accounts',
                'processed_account_count': data['processed_account_count'],
                'count': len(data['shops']),
                'stores': data['shops'],
                'errors': data['errors'],
            },
            status=status.HTTP_200_OK,
        )


class ZohoCommerceShopProductListAPIView(APIView):
    """
    GET — list products for a selected Zoho shop id.
    """

    def get(self, request, shop_id: str):
        account = (request.query_params.get('account') or 'primary').strip().lower()
        page = request.query_params.get('page', 1)
        per_page = request.query_params.get('per_page', 20)
        try:
            shop, products = fetch_zoho_shop_products(
                shop_id, page=page, per_page=per_page, account=account,
            )
        except ZohoCommerceError as e:
            msg = str(e)
            st = status.HTTP_404_NOT_FOUND if 'not found' in msg.lower() else status.HTTP_503_SERVICE_UNAVAILABLE
            return Response({'status': 'error', 'message': msg}, status=st)
        return Response(
            {
                'status': 'success',
                'message': 'Products fetched successfully',
                'account': account,
                'organization_id': shop.get('organization_id', ''),
                'shop': shop,
                'count': len(products),
                'products': products,
            },
            status=status.HTTP_200_OK,
        )


class ZohoCommerceProductsProxyAPIView(APIView):
    """
    GET — forwards query string to Zoho Commerce list products API; response body is Zoho JSON.

    Query (optional): ``store_id`` (local Store pk — uses ``zoho_org_id`` for org header),
    filter_by, sort_column, sort_order, page_start_from, per_page
    """

    def get(self, request):
        store, err = _optional_store_for_zoho_proxy(request)
        if err:
            return err
        url = build_products_list_url(dict(request.query_params))
        try:
            http_status, payload = zoho_commerce_proxy_get(url, store=store)
        except ZohoCommerceProductError as e:
            return Response({'detail': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if isinstance(payload, (dict, list)):
            return Response(payload, status=http_status)
        return Response({'detail': payload}, status=http_status)


class ZohoCommerceProductDetailProxyAPIView(APIView):
    """
    GET — Zoho Commerce product edit-page API (full product payload for one product_id).

    Query (optional): ``store_id`` — same as list proxy.
    """

    def get(self, request, product_id: str):
        store, err = _optional_store_for_zoho_proxy(request)
        if err:
            return err
        try:
            url = build_product_editpage_url(product_id)
            http_status, payload = zoho_commerce_proxy_get(url, store=store)
        except ZohoCommerceProductError as e:
            msg = str(e)
            st = (
                status.HTTP_400_BAD_REQUEST
                if 'required' in msg.lower()
                else status.HTTP_503_SERVICE_UNAVAILABLE
            )
            return Response({'detail': msg}, status=st)
        if isinstance(payload, (dict, list)):
            return Response(payload, status=http_status)
        return Response({'detail': payload}, status=http_status)


class StoreProductDetailAPIView(APIView):
    """
    GET — single product; store_id must match the product's store (safe for scoped IDs).
    """

    def get(self, request, store_id, pk):
        store = get_object_or_404(Store, pk=store_id, is_active=True)
        product = get_object_or_404(
            Product.objects.select_related('store'),
            pk=pk,
            store=store,
            is_active=True,
        )
        return Response(ProductDetailSerializer(product).data, status=status.HTTP_200_OK)


class RelatedProductSuggestionListAPIView(generics.ListAPIView):
    """
    GET — related product suggestions for one product in a store.

    Query:
    - limit (optional, default 10, max 20)
    """

    serializer_class = ProductListSerializer

    def _resolve_ids(self):
        store_id = self.kwargs.get('store_id')
        product_id = self.kwargs.get('pk')
        if store_id is None:
            store_id = self.request.query_params.get('store_id')
        if product_id is None:
            product_id = self.request.query_params.get('product_id')

        if store_id in (None, ''):
            raise ValidationError({'store_id': 'This query parameter is required.'})
        if product_id in (None, ''):
            raise ValidationError({'product_id': 'This query parameter is required.'})

        try:
            store_id = int(store_id)
        except (TypeError, ValueError):
            raise ValidationError({'store_id': 'Must be an integer.'})
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            raise ValidationError({'product_id': 'Must be an integer.'})
        return store_id, product_id

    def get_queryset(self):
        store_id, product_id = self._resolve_ids()
        store = get_object_or_404(Store, pk=store_id, is_active=True)
        product = get_object_or_404(
            Product,
            pk=product_id,
            store=store,
            is_active=True,
        )

        raw_limit = self.request.query_params.get('limit', 10)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 20))

        base_qs = Product.objects.filter(
            store=store,
            is_active=True,
        ).select_related('store').exclude(pk=product.pk)

        if product.category:
            related_qs = list(base_qs.filter(category__iexact=product.category).order_by('name')[:limit])
            if len(related_qs) >= limit:
                return related_qs
            needed = limit - len(related_qs)
            fallback_qs = list(
                base_qs.exclude(pk__in=[p.pk for p in related_qs]).order_by('name')[:needed]
            )
            return related_qs + fallback_qs

        return base_qs.order_by('name')[:limit]


class AdminStoreListCreateAPIView(generics.ListCreateAPIView):
    """
    Staff only (JWT + is_staff). GET all stores; POST create a store.
    """
    permission_classes = [IsAdminUser]
    queryset = Store.objects.all().order_by('sort_order', 'name')
    serializer_class = StoreAdminSerializer


class AdminStoreDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    """Staff only. GET/PATCH/DELETE one store by id."""
    permission_classes = [IsAdminUser]
    queryset = Store.objects.all()
    serializer_class = StoreAdminSerializer


class AdminStoreProductListCreateAPIView(generics.ListCreateAPIView):
    """
    Staff only. GET all products for a store (including inactive); POST add product mapped to this store.
    """
    permission_classes = [IsAdminUser]
    serializer_class = ProductAdminSerializer

    def get_queryset(self):
        store = get_object_or_404(Store, pk=self.kwargs['store_id'])
        return Product.objects.filter(store=store).select_related('store').order_by('name')

    def perform_create(self, serializer):
        store = get_object_or_404(Store, pk=self.kwargs['store_id'])
        serializer.save(store=store)


class AdminStoreProductDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    """Staff only. GET/PATCH/DELETE product; must belong to store_id in URL."""
    permission_classes = [IsAdminUser]
    serializer_class = ProductAdminSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        return Product.objects.filter(store_id=self.kwargs['store_id']).select_related('store')

