from django.urls import path
from .views import (
    SuperuserLoginView,
    OrganizationListView,
    ListCouponsView,
    CreateCouponView,
    GetCouponView,
    UpdateCouponView,
    DeleteCouponView,
)

app_name = 'offers'

urlpatterns = [
    # ── Existing ───────────────────────────────────────────────
    path('superuser-login/', SuperuserLoginView.as_view(), name='superuser-login'),

    # ── Organizations ───────────────────────────────────────────
    path('organizations/', OrganizationListView.as_view(), name='org-list'),

    # ── Coupons ─────────────────────────────────────────────────
    path('organizations/<int:org_id>/coupons/', ListCouponsView.as_view(), name='list-coupons'),
    path('organizations/<int:org_id>/coupons/create/', CreateCouponView.as_view(), name='create-coupon'),
    path('organizations/<int:org_id>/coupons/delete/', DeleteCouponView.as_view(), name='delete-coupon'),
    path('organizations/<int:org_id>/coupons/<str:coupon_id>/', GetCouponView.as_view(), name='get-coupon'),
    path('organizations/<int:org_id>/coupons/<str:coupon_id>/update/', UpdateCouponView.as_view(), name='update-coupon'),
]