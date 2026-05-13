from decimal import Decimal

from django.db import models
from django.utils import timezone


class Coupon(models.Model):
    coupon_id = models.CharField(max_length=120)
    couponset_id = models.CharField(max_length=120, blank=True)
    org_id = models.IntegerField(db_index=True)
    coupon_name = models.CharField(max_length=255, blank=True)
    coupon_code = models.CharField(max_length=120, db_index=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=False)
    status = models.CharField(max_length=120, blank=True)
    rule_type = models.CharField(max_length=120, blank=True)
    coupon_type = models.CharField(max_length=120, blank=True)
    show_in_storefront = models.BooleanField(default=False)
    restrict_for_guest_user = models.BooleanField(default=False)
    restrict_for_offline_payments = models.BooleanField(default=False)
    stop_after_this_rule = models.BooleanField(default=False)
    apply_once_per_order = models.BooleanField(default=False)
    type = models.CharField(max_length=120, blank=True)
    duration = models.CharField(max_length=120, blank=True)
    discount_type = models.CharField(max_length=120, blank=True)
    discount_by = models.CharField(max_length=120, blank=True)
    apply_on = models.CharField(max_length=120, blank=True)
    discount_value = models.CharField(max_length=120, blank=True)
    discount_amounts = models.JSONField(default=list, blank=True)
    max_discount_amount = models.CharField(max_length=120, blank=True)
    max_redemption = models.IntegerField(default=0)
    max_redemption_count = models.IntegerField(default=0)
    redemption_count = models.IntegerField(default=0)
    max_redemption_count_per_user = models.IntegerField(default=0)
    max_usage_per_transaction = models.IntegerField(default=0)
    max_discounted_product_count_per_cart = models.CharField(max_length=120, blank=True)
    minimum_order_value = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    minimum_order_quantity = models.CharField(max_length=120, blank=True)
    activation_time = models.DateTimeField(null=True, blank=True)
    expiry_at = models.CharField(max_length=120, blank=True)
    expiry_time = models.DateTimeField(null=True, blank=True)
    eligible_products = models.JSONField(default=dict, blank=True)
    buy_products = models.JSONField(default=dict, blank=True)
    get_products = models.JSONField(default=dict, blank=True)
    eligible_customers = models.JSONField(default=dict, blank=True)
    eligible_shipping_zones = models.JSONField(default=dict, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'offer_coupon'
        unique_together = ('coupon_id', 'org_id')
        ordering = ['-last_synced_at', '-created_at']

    def __str__(self):
        return f'{self.coupon_code} ({self.org_id})'

    def is_expired(self):
        if not self.expiry_time:
            return False
        return timezone.now() >= self.expiry_time


class CouponUsageLog(models.Model):
    user_id = models.IntegerField(db_index=True)
    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name='usage_logs')
    coupon_id_str = models.CharField(max_length=120)
    coupon_code = models.CharField(max_length=120, db_index=True)
    org_id = models.IntegerField(db_index=True)
    order_id = models.IntegerField(db_index=True)
    discount_amount_applied = models.DecimalField(max_digits=15, decimal_places=3)
    coupon_type = models.CharField(max_length=120, blank=True)
    discount_type = models.CharField(max_length=120, blank=True)
    used_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'offer_coupon_usage_log'
        ordering = ['-used_at']

    def __str__(self):
        return f'{self.coupon_code} used by {self.user_id}'
