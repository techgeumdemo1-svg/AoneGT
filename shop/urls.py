from django.urls import path
from .views import (
    CartDetailAPIView,
    CartAddItemAPIView,
    CartItemDetailAPIView,
    CheckoutAPIView,
    OrderListAPIView,
    OrderDetailAPIView,
)

urlpatterns = [
    path('cart/', CartDetailAPIView.as_view(), name='shop-cart'),
    path('cart/items/', CartAddItemAPIView.as_view(), name='shop-cart-add-item'),
    path('cart/items/<int:pk>/', CartItemDetailAPIView.as_view(), name='shop-cart-item'),
    path('orders/checkout/', CheckoutAPIView.as_view(), name='shop-checkout'),
    path('orders/', OrderListAPIView.as_view(), name='shop-order-list'),
    path('orders/<int:pk>/', OrderDetailAPIView.as_view(), name='shop-order-detail'),
]
