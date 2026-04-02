from django.urls import path
from .views import (
    StoreListAPIView,
    StoreProductListAPIView,
    StoreProductDetailAPIView,
    AdminStoreListCreateAPIView,
    AdminStoreDetailAPIView,
    AdminStoreProductListCreateAPIView,
    AdminStoreProductDetailAPIView,
)

urlpatterns = [
    path('admin/stores/', AdminStoreListCreateAPIView.as_view(), name='catalog-admin-store-list-create'),
    path('admin/stores/<int:pk>/', AdminStoreDetailAPIView.as_view(), name='catalog-admin-store-detail'),
    path(
        'admin/stores/<int:store_id>/products/',
        AdminStoreProductListCreateAPIView.as_view(),
        name='catalog-admin-store-products',
    ),
    path(
        'admin/stores/<int:store_id>/products/<int:pk>/',
        AdminStoreProductDetailAPIView.as_view(),
        name='catalog-admin-store-product-detail',
    ),
    path('stores/', StoreListAPIView.as_view(), name='catalog-store-list'),
    path(
        'stores/<int:store_id>/products/',
        StoreProductListAPIView.as_view(),
        name='catalog-store-products',
    ),
    path(
        'stores/<int:store_id>/products/<int:pk>/',
        StoreProductDetailAPIView.as_view(),
        name='catalog-store-product-detail',
    ),
]
