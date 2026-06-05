from django.urls import path
from .customers import (
    AdminCustomerDetailAPIView,
    AdminCustomerListAPIView,
    AdminCustomerOrdersAPIView,
    AdminCustomerStatusUpdateAPIView,
    AdminCustomerSuperCoinsAPIView,
)
from .orders import (
    AdminOrderDetailAPIView,
    AdminOrderInvoiceAPIView,
    AdminOrderListAPIView,
    AdminOrderStatusUpdateAPIView,
    AdminOrderTimelineAPIView,
    AdminOrderVerifyPaymentAPIView,
)
from .returns import (
    AdminReturnApproveAPIView,
    AdminReturnDetailAPIView,
    AdminReturnListAPIView,
    AdminReturnRefundAPIView,
    AdminReturnRejectAPIView,
)
from .stores import (
    AdminStoreListAPIView,
    AdminStoreReorderAPIView,
    AdminStoreVisibilityUpdateAPIView,
)
from .views import (
    AdminDashboardChartsAPIView,
    AdminDashboardSummaryAPIView,
    AdminForgotPasswordAPIView,
    AdminLoginAPIView,
    AdminLoginVerifyOTPAPIView,
    AdminLogoutAPIView,
    AdminMeAPIView,
    AdminResetPasswordAPIView,
)

urlpatterns = [
    path('auth/login/', AdminLoginAPIView.as_view(), name='admin-auth-login'),
    path('auth/login/verify-otp/', AdminLoginVerifyOTPAPIView.as_view(), name='admin-auth-login-verify-otp'),
    path('auth/logout/', AdminLogoutAPIView.as_view(), name='admin-auth-logout'),
    path('auth/forgot-password/', AdminForgotPasswordAPIView.as_view(), name='admin-auth-forgot-password'),
    path('auth/reset-password/', AdminResetPasswordAPIView.as_view(), name='admin-auth-reset-password'),
    path('auth/me/', AdminMeAPIView.as_view(), name='admin-auth-me'),
    path('dashboard/summary/', AdminDashboardSummaryAPIView.as_view(), name='admin-dashboard-summary'),
    path('dashboard/charts/', AdminDashboardChartsAPIView.as_view(), name='admin-dashboard-charts'),
    path('orders/', AdminOrderListAPIView.as_view(), name='admin-orders-list'),
    path('orders/<int:pk>/', AdminOrderDetailAPIView.as_view(), name='admin-orders-detail'),
    path('orders/<int:pk>/status/', AdminOrderStatusUpdateAPIView.as_view(), name='admin-orders-status'),
    path('orders/<int:pk>/timeline/', AdminOrderTimelineAPIView.as_view(), name='admin-orders-timeline'),
    path('orders/<int:pk>/invoice/', AdminOrderInvoiceAPIView.as_view(), name='admin-orders-invoice'),
    path('orders/<int:pk>/verify-payment/', AdminOrderVerifyPaymentAPIView.as_view(), name='admin-orders-verify-payment'),
    path('customers/', AdminCustomerListAPIView.as_view(), name='admin-customers-list'),
    path('customers/<int:pk>/', AdminCustomerDetailAPIView.as_view(), name='admin-customers-detail'),
    path('customers/<int:pk>/status/', AdminCustomerStatusUpdateAPIView.as_view(), name='admin-customers-status'),
    path('customers/<int:pk>/orders/', AdminCustomerOrdersAPIView.as_view(), name='admin-customers-orders'),
    path('customers/<int:pk>/super-coins/', AdminCustomerSuperCoinsAPIView.as_view(), name='admin-customers-super-coins'),
    path('returns/', AdminReturnListAPIView.as_view(), name='admin-returns-list'),
    path('returns/<int:pk>/', AdminReturnDetailAPIView.as_view(), name='admin-returns-detail'),
    path('returns/<int:pk>/approve/', AdminReturnApproveAPIView.as_view(), name='admin-returns-approve'),
    path('returns/<int:pk>/reject/', AdminReturnRejectAPIView.as_view(), name='admin-returns-reject'),
    path('returns/<int:pk>/refund/', AdminReturnRefundAPIView.as_view(), name='admin-returns-refund'),
    path('stores/', AdminStoreListAPIView.as_view(), name='admin-stores-list'),
    path('stores/reorder/', AdminStoreReorderAPIView.as_view(), name='admin-stores-reorder'),
    path('stores/<int:pk>/visibility/', AdminStoreVisibilityUpdateAPIView.as_view(), name='admin-stores-visibility'),
]