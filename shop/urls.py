from django.urls import path

from .views import (
    UserAddressListCreateAPIView,
    UserAddressDetailAPIView,
    WishlistListCreateAPIView,
    WishlistItemDetailAPIView,
    WishlistMoveToCartAPIView,
    CartDetailAPIView,
    CartClearAPIView,
    CartAddItemAPIView,
    CartItemDetailAPIView,
    CheckoutAPIView,
    OrderListAPIView,
    OrderDetailAPIView,
    OrderReturnListCreateAPIView,
    OrderReorderAPIView,
    ZohoProductListAPIView,
    ZohoProductDetailAPIView,
    ZohoProductImageProxyAPIView,
)

urlpatterns = [
    path('addresses/', UserAddressListCreateAPIView.as_view(), name='shop-address-list-create'),
    path('addresses/<int:pk>/', UserAddressDetailAPIView.as_view(), name='shop-address-detail'),
    path('wishlist/', WishlistListCreateAPIView.as_view(), name='shop-wishlist-list-create'),
    path('wishlist/<int:pk>/', WishlistItemDetailAPIView.as_view(), name='shop-wishlist-detail'),
    path('wishlist/<int:pk>/move-to-cart/', WishlistMoveToCartAPIView.as_view(), name='shop-wishlist-move-to-cart'),
    path('cart/', CartDetailAPIView.as_view(), name='shop-cart'),
    path('cart/clear/', CartClearAPIView.as_view(), name='shop-cart-clear'),
    path('cart/items/', CartAddItemAPIView.as_view(), name='shop-cart-add-item'),
    path('cart/items/<int:pk>/', CartItemDetailAPIView.as_view(), name='shop-cart-item'),
    path('orders/checkout/', CheckoutAPIView.as_view(), name='shop-checkout'),
    path('orders/<int:pk>/returns/', OrderReturnListCreateAPIView.as_view(), name='shop-order-returns'),
    path('orders/<int:pk>/reorder/', OrderReorderAPIView.as_view(), name='shop-order-reorder'),
    path('orders/', OrderListAPIView.as_view(), name='shop-order-list'),
    path('orders/<int:pk>/', OrderDetailAPIView.as_view(), name='shop-order-detail'),

    path('zoho-products/', ZohoProductListAPIView.as_view(), name='zoho-product-list'),
    path('zoho-products/<str:product_id>/', ZohoProductDetailAPIView.as_view(), name='zoho-product-detail'),
    path(
        'zoho-products/<str:product_id>/image/',
        ZohoProductImageProxyAPIView.as_view(),
        name='zoho-product-image-proxy',
    ),

]
