from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Store, Product
from .serializers import (
    StoreListSerializer,
    ProductListSerializer,
    ProductDetailSerializer,
    StoreAdminSerializer,
    ProductAdminSerializer,
)


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
