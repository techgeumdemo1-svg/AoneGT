from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Store, Product
from .services.zoho_commerce_products import (
    ZohoCommerceProductError,
    build_product_editpage_url,
    build_products_list_url,
    zoho_commerce_proxy_get,
)
from .serializers import (
    StoreListSerializer,
    ProductListSerializer,
    ProductDetailSerializer,
    StoreAdminSerializer,
    ProductAdminSerializer,
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
