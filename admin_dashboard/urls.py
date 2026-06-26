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
    AdminOrderCollectCardAPIView,
    AdminOrderCollectCodAPIView,
    AdminCancelledOrderListAPIView,
    AdminOrderCreateInvoiceAPIView,
    AdminOrderDetailAPIView,
    AdminOrderGeideaCollectAPIView,
    AdminOrderGeideaReconcileAPIView,
    AdminOrderListAPIView,
    AdminOrderMarkCodPaidAPIView,
    AdminOrderStatusUpdateAPIView,
    AdminOrderTimelineAPIView,
    AdminOrderVerifyPaymentAPIView,
)
from .returns import (
    AdminReturnApproveAPIView,
    AdminReturnListAPIView,
    AdminReturnLogsAPIView,
    AdminReturnRefundAPIView,
    AdminReturnRejectAPIView,
    AdminReturnZohoSyncAPIView,
)
from .banners import (
    AdminBannerListCreateAPIView,
    AdminBannerReorderAPIView,
)
from .cms import (
    AdminCMSPageListAPIView,
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
from .roles import (
    AdminPermissionListAPIView,
    AdminRoleDetailAPIView,
    AdminRoleListCreateAPIView,
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
    AdminUserDeleteAPIView,
    AdminUserDetailUpdateAPIView,
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
    path('orders/cancelled/', AdminCancelledOrderListAPIView.as_view(), name='admin-orders-cancelled'),
    path('orders/detail/', AdminOrderDetailAPIView.as_view(), name='admin-orders-detail'),
    path('orders/status/', AdminOrderStatusUpdateAPIView.as_view(), name='admin-orders-status'),
    path('orders/collect-cod/', AdminOrderCollectCodAPIView.as_view(), name='admin-orders-collect-cod'),
    path('orders/timeline/', AdminOrderTimelineAPIView.as_view(), name='admin-orders-timeline'),
    path('orders/geidea-collect/', AdminOrderGeideaCollectAPIView.as_view(), name='admin-orders-geidea-collect'),
    path('orders/collect-card/', AdminOrderCollectCardAPIView.as_view(), name='admin-orders-collect-card'),
    path('orders/geidea-reconcile/', AdminOrderGeideaReconcileAPIView.as_view(), name='admin-orders-geidea-reconcile'),
    path('orders/create-invoice/', AdminOrderCreateInvoiceAPIView.as_view(), name='admin-orders-create-invoice'),
    path('orders/verify-payment/', AdminOrderVerifyPaymentAPIView.as_view(), name='admin-orders-verify-payment'),
    path('orders/mark-cod-paid/', AdminOrderMarkCodPaidAPIView.as_view(), name='admin-orders-mark-cod-paid'),
    path('customers/', AdminCustomerListAPIView.as_view(), name='admin-customers-list'),
    path('customers/detail/', AdminCustomerDetailAPIView.as_view(), name='admin-customers-detail'),
    path('customers/status/', AdminCustomerStatusUpdateAPIView.as_view(), name='admin-customers-status'),
    path('customers/orders/', AdminCustomerOrdersAPIView.as_view(), name='admin-customers-orders'),
    path('customers/super-coins/', AdminCustomerSuperCoinsAPIView.as_view(), name='admin-customers-super-coins'),
    path('returns/', AdminReturnListAPIView.as_view(), name='admin-returns-list'),
    path('returns/logs/', AdminReturnLogsAPIView.as_view(), name='admin-returns-logs'),
    path('returns/approve/', AdminReturnApproveAPIView.as_view(), name='admin-returns-approve'),
    path('returns/reject/', AdminReturnRejectAPIView.as_view(), name='admin-returns-reject'),
    path('returns/refund/', AdminReturnRefundAPIView.as_view(), name='admin-returns-refund'),
    path('returns/zoho-sync/', AdminReturnZohoSyncAPIView.as_view(), name='admin-returns-zoho-sync'),
    path('stores/', AdminStoreListAPIView.as_view(), name='admin-stores-list'),
    path('stores/reorder/', AdminStoreReorderAPIView.as_view(), name='admin-stores-reorder'),
    path('stores/visibility/', AdminStoreVisibilityUpdateAPIView.as_view(), name='admin-stores-visibility'),
    path('banners/', AdminBannerListCreateAPIView.as_view(), name='admin-banners-list-create'),
    path('banners/reorder/', AdminBannerReorderAPIView.as_view(), name='admin-banners-reorder'),
    path('cms/faqs/', AdminFAQListCreateAPIView.as_view(), name='admin-cms-faqs-list-create'),
    path('cms/pages/', AdminCMSPageListAPIView.as_view(), name='admin-cms-pages-list'),
    path('users/', AdminUserListCreateAPIView.as_view(), name='admin-users-list-create'),
    path('users/<int:pk>/', AdminUserDetailUpdateAPIView.as_view(), name='admin-users-detail'),
    path('users/delete/', AdminUserDeleteAPIView.as_view(), name='admin-users-delete'),
    path('users/deactivate/', AdminUserDeactivateAPIView.as_view(), name='admin-users-deactivate'),
    path('users/reactivate/', AdminUserReactivateAPIView.as_view(), name='admin-users-reactivate'),
    path('roles/', AdminRoleListCreateAPIView.as_view(), name='admin-roles-list-create'),
    path('roles/detail/', AdminRoleDetailAPIView.as_view(), name='admin-roles-detail'),
    path('roles/permissions/', AdminPermissionListAPIView.as_view(), name='admin-roles-permissions'),
    path('reports/cart-abandonment/', AdminCartAbandonmentReportAPIView.as_view(), name='admin-reports-cart-abandonment'),
    path('reports/refunds/', AdminRefundsReportAPIView.as_view(), name='admin-reports-refunds'),
    path('reports/export/excel/', AdminReportExcelExportAPIView.as_view(), name='admin-reports-export-excel'),
    path('reports/export/pdf/', AdminReportPdfExportAPIView.as_view(), name='admin-reports-export-pdf'),
    path('transactions/', AdminTransactionListAPIView.as_view(), name='admin-transactions-list'),
    path('transactions/detail/', AdminTransactionDetailAPIView.as_view(), name='admin-transactions-detail'),
    path('delivery-zones/', AdminDeliveryZoneListCreateAPIView.as_view(), name='admin-delivery-zones-list-create'),
    path('delivery-zones/detail/', AdminDeliveryZoneDetailAPIView.as_view(), name='admin-delivery-zones-detail'),
    path('delivery-zones/toggle/', AdminDeliveryZoneToggleAPIView.as_view(), name='admin-delivery-zones-toggle'),
    path('super-coins/summary/', AdminSuperCoinsSummaryAPIView.as_view(), name='admin-super-coins-summary'),
    path('super-coins/settings/', AdminSuperCoinsSettingsAPIView.as_view(), name='admin-super-coins-settings'),
    path('super-coins/customers/', AdminSuperCoinsCustomerAPIView.as_view(), name='admin-super-coins-customer'),
    path('activity-logs/', AdminActivityLogListAPIView.as_view(), name='admin-activity-logs-list'),

    # Finance — Zoho Books store config and journal audit logs
    path('finance/store-config/', AdminFinanceStoreConfigListAPIView.as_view(), name='admin-finance-store-config-list'),
    path('finance/store-config/<int:store_id>/', AdminFinanceStoreConfigDetailAPIView.as_view(), name='admin-finance-store-config-detail'),
    path('finance/journals/', AdminFinanceJournalListAPIView.as_view(), name='admin-finance-journals-list'),
    path('finance/journals/<int:pk>/retry/', AdminFinanceJournalRetryAPIView.as_view(), name='admin-finance-journals-retry'),
]