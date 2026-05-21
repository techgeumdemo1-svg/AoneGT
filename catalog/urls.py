from django.urls import path
from .views import (
    BannerListAPIView,
    BannerAdminListCreateAPIView,
    BannerAdminDetailAPIView,
    RelatedProductSuggestionListAPIView,
    StoreListAPIView,
    StoreProductListAPIView,
    StoreProductReviewListCreateAPIView,
    StoreProductRatingAPIView,
    StoreProductDetailAPIView,
    ZohoCommerceShopListAPIView,
    ZohoCommerceShopProductListAPIView,
    ZohoCommerceProductsProxyAPIView,
    ZohoCommerceProductDetailProxyAPIView,
    AdminStoreListCreateAPIView,
    AdminStoreDetailAPIView,
    AdminStoreProductListCreateAPIView,
    AdminStoreProductDetailAPIView,
)

urlpatterns = [
    path('banners/', BannerListAPIView.as_view(), name='catalog-banners-list'),
    path('admin/banners/', BannerAdminListCreateAPIView.as_view(), name='catalog-admin-banners-list-create'),
    path('admin/banners/<int:pk>/', BannerAdminDetailAPIView.as_view(), name='catalog-admin-banner-detail'),
    path(
        'zoho/shops/',
        ZohoCommerceShopListAPIView.as_view(),
        name='catalog-zoho-shop-list',
    ),
    path(
        'zoho/shops/<str:shop_id>/products/',
        ZohoCommerceShopProductListAPIView.as_view(),
        name='catalog-zoho-shop-product-list',
    ),
    path(
        'zoho-commerce/products/',
        ZohoCommerceProductsProxyAPIView.as_view(),
        name='catalog-zoho-commerce-products-proxy',
    ),
    path(
        'zoho-commerce/products/<str:product_id>/',
        ZohoCommerceProductDetailProxyAPIView.as_view(),
        name='catalog-zoho-commerce-product-proxy',
    ),
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
        'stores/products/reviews/',
        StoreProductReviewListCreateAPIView.as_view(),
        name='catalog-store-product-reviews',
    ),
    path(
        'stores/products/rating/',
        StoreProductRatingAPIView.as_view(),
        name='catalog-store-product-rating',
    ),
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
    path(
        'products/related/',
        RelatedProductSuggestionListAPIView.as_view(),
        name='catalog-related-product-suggestions-query',
    ),
    
]
