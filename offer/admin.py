from django.contrib import admin

from .models import Coupon, CouponUsageLog


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = (
        'coupon_code', 'coupon_name', 'org_id', 'coupon_type', 'rule_type',
        'is_active', 'redemption_count', 'expiry_time',
    )
    search_fields = ('coupon_code', 'coupon_name', 'coupon_id')
    list_filter = ('org_id', 'is_active', 'coupon_type', 'rule_type')


@admin.register(CouponUsageLog)
class CouponUsageLogAdmin(admin.ModelAdmin):
    list_display = ('coupon_code', 'user_id', 'org_id', 'order_id', 'discount_amount_applied', 'used_at')
    search_fields = ('coupon_code', 'coupon_id_str', 'user_id', 'order_id')
    list_filter = ('org_id', 'coupon_type', 'discount_type')
