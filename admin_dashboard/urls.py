from django.urls import path
from .activity_logs import AdminActivityLogListAPIView
from .delivery_zones import (
    AdminDeliveryZoneDetailAPIView,
    AdminDeliveryZoneListCreateAPIView,
    AdminDeliveryZoneToggleAPIView,
)
from .customers import (
    AdminCustomerDetailAPIView,
    AdminCustomerListAPIView,
    AdminCustomerOrdersAPIView,
    AdminCustomerStatusUpdateAPIView,
    AdminCustomerSuperCoinsAPIView,
)
from .finance import (
    AdminFinanceJournalListAPIView,
    AdminFinanceJournalRetryAPIView,
    AdminFinanceStoreConfigDetailAPIView,
    AdminFinanceStoreConfigListAPIView,
)
from .orders import (
    AdminOrderDetailAPIView,
    AdminOrderInvoiceAPIView,
    AdminOrderListAPIView,
    AdminOrderMarkCodPaidAPIView,
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
from .banners import (
    AdminBannerDetailAPIView,
    AdminBannerListCreateAPIView,
    AdminBannerReorderAPIView,
)
from .cms import (
    AdminCMSPageDetailAPIView,
    AdminCMSPageListAPIView,
    AdminFAQDetailAPIView,
    AdminFAQListCreateAPIView,
)
from .stores import (
    AdminStoreListAPIView,
    AdminStoreReorderAPIView,
    AdminStoreVisibilityUpdateAPIView,
)
from .reports import (
    AdminCartAbandonmentReportAPIView,
    AdminRefundsReportAPIView,
    AdminReportExcelExportAPIView,
    AdminReportPdfExportAPIView,
)
from .super_coins import (
    AdminSuperCoinsCustomerAPIView,
    AdminSuperCoinsSettingsAPIView,
    AdminSuperCoinsSummaryAPIView,
)
from .transactions import (
    AdminTransactionDetailAPIView,
    AdminTransactionListAPIView,
)
from .users import (
    AdminUserDeactivateAPIView,
    AdminUserDetailAPIView,
    AdminUserListCreateAPIView,
    AdminUserReactivateAPIView,
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
    path('orders/<int:pk>/mark-cod-paid/', AdminOrderMarkCodPaidAPIView.as_view(), name='admin-orders-mark-cod-paid'),
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
    path('banners/', AdminBannerListCreateAPIView.as_view(), name='admin-banners-list-create'),
    path('banners/reorder/', AdminBannerReorderAPIView.as_view(), name='admin-banners-reorder'),
    path('banners/<int:pk>/', AdminBannerDetailAPIView.as_view(), name='admin-banners-detail'),
    path('cms/faqs/', AdminFAQListCreateAPIView.as_view(), name='admin-cms-faqs-list-create'),
    path('cms/faqs/<int:pk>/', AdminFAQDetailAPIView.as_view(), name='admin-cms-faqs-detail'),
    path('cms/pages/', AdminCMSPageListAPIView.as_view(), name='admin-cms-pages-list'),
    path('cms/pages/<int:pk>/', AdminCMSPageDetailAPIView.as_view(), name='admin-cms-pages-detail'),
    path('users/', AdminUserListCreateAPIView.as_view(), name='admin-users-list-create'),
    path('users/<int:pk>/deactivate/', AdminUserDeactivateAPIView.as_view(), name='admin-users-deactivate'),
    path('users/<int:pk>/reactivate/', AdminUserReactivateAPIView.as_view(), name='admin-users-reactivate'),
    path('users/<int:pk>/', AdminUserDetailAPIView.as_view(), name='admin-users-detail'),
    path('reports/cart-abandonment/', AdminCartAbandonmentReportAPIView.as_view(), name='admin-reports-cart-abandonment'),
    path('reports/refunds/', AdminRefundsReportAPIView.as_view(), name='admin-reports-refunds'),
    path('reports/export/excel/', AdminReportExcelExportAPIView.as_view(), name='admin-reports-export-excel'),
    path('reports/export/pdf/', AdminReportPdfExportAPIView.as_view(), name='admin-reports-export-pdf'),
    path('transactions/', AdminTransactionListAPIView.as_view(), name='admin-transactions-list'),
    path('transactions/<int:pk>/', AdminTransactionDetailAPIView.as_view(), name='admin-transactions-detail'),
    path('delivery-zones/', AdminDeliveryZoneListCreateAPIView.as_view(), name='admin-delivery-zones-list-create'),
    path('delivery-zones/<int:pk>/toggle/', AdminDeliveryZoneToggleAPIView.as_view(), name='admin-delivery-zones-toggle'),
    path('delivery-zones/<int:pk>/', AdminDeliveryZoneDetailAPIView.as_view(), name='admin-delivery-zones-detail'),
    path('super-coins/summary/', AdminSuperCoinsSummaryAPIView.as_view(), name='admin-super-coins-summary'),
    path('super-coins/settings/', AdminSuperCoinsSettingsAPIView.as_view(), name='admin-super-coins-settings'),
    path('super-coins/customers/<int:customer_id>/', AdminSuperCoinsCustomerAPIView.as_view(), name='admin-super-coins-customer'),
    path('activity-logs/', AdminActivityLogListAPIView.as_view(), name='admin-activity-logs-list'),

    # Finance — Zoho Books store config and journal audit logs
    path('finance/store-config/', AdminFinanceStoreConfigListAPIView.as_view(), name='admin-finance-store-config-list'),
    path('finance/store-config/<int:store_id>/', AdminFinanceStoreConfigDetailAPIView.as_view(), name='admin-finance-store-config-detail'),
    path('finance/journals/', AdminFinanceJournalListAPIView.as_view(), name='admin-finance-journals-list'),
    path('finance/journals/<int:pk>/retry/', AdminFinanceJournalRetryAPIView.as_view(), name='admin-finance-journals-retry'),
]