from django.urls import path

from .views import CheckoutCouponsAPIView, OrderSummaryAPIView

urlpatterns = [
    path('checkout-coupons/', CheckoutCouponsAPIView.as_view(), name='offer-checkout-coupons'),
    path('order-summary/', OrderSummaryAPIView.as_view(), name='offer-order-summary'),
]
