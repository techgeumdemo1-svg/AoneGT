from rest_framework import serializers


# ── Existing (do not remove) ──────────────────────────────────────────────────

class SuperuserLoginSerializer(serializers.Serializer):
    """
    Handles basic input validation for superuser login credentials.
    Actual authentication is deferred to the service layer.
    """
    email = serializers.EmailField(required=True)
    password = serializers.CharField(
        write_only=True, required=True, style={'input_type': 'password'}
    )


# ── Organization ──────────────────────────────────────────────────────────────

class WebhookConfigSerializer(serializers.Serializer):
    webhook_type = serializers.CharField()
    is_active = serializers.BooleanField()


class OrganizationSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField()
    org_id = serializers.CharField()
    is_active = serializers.BooleanField()
    image_url = serializers.SerializerMethodField()
    webhooks = WebhookConfigSerializer(many=True, read_only=True)

    def get_image_url(self, obj):
        request = self.context.get('request')
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return None


# ── Coupons ───────────────────────────────────────────────────────────────────

class CouponCreateSerializer(serializers.Serializer):
    # Basic
    apply_as = serializers.ChoiceField(choices=['coupon', 'automatic_discount'])
    coupon_code = serializers.CharField(required=False, allow_blank=True, default='')
    coupon_name = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True, default='')
    is_active = serializers.BooleanField(default=True)
    show_in_storefront = serializers.BooleanField(default=False)

    # Discount type
    discount_type = serializers.ChoiceField(choices=[
        'order_flat',
        'order_percentage',
        'item_flat',
        'item_percentage',
        'free_shipping',
        'buy_x_get_y',
    ])
    discount_value = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False
    )
    max_discount_amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False
    )

    # item_flat / item_percentage: [{"product_id": "xxx"}, ...]
    eligible_product_ids = serializers.ListField(
        child=serializers.DictField(), required=False, default=list
    )

    # buy_x_get_y
    buying_quantity = serializers.IntegerField(required=False)
    buy_product_ids = serializers.ListField(
        child=serializers.DictField(), required=False, default=list
    )
    get_product_ids = serializers.ListField(
        child=serializers.DictField(), required=False, default=list
    )

    # Cart minimum
    minimum_cart_amount_enabled = serializers.BooleanField(default=False)
    minimum_cart_amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False
    )

    # Validity
    valid_from = serializers.CharField()
    never_expires = serializers.BooleanField(default=False)
    valid_till = serializers.CharField(required=False, allow_blank=True, default='')

    # Limits
    store_limit_enabled = serializers.BooleanField(default=False)
    store_limit = serializers.IntegerField(required=False)
    customer_limit_enabled = serializers.BooleanField(default=False)
    customer_limit = serializers.IntegerField(required=False)

    def validate(self, data):
        if data.get('apply_as') == 'coupon' and not data.get('coupon_code'):
            raise serializers.ValidationError(
                {"coupon_code": "Required when apply_as is 'coupon'."}
            )
        if not data.get('never_expires') and not data.get('valid_till'):
            raise serializers.ValidationError(
                {"valid_till": "Required when never_expires is false."}
            )
        if data.get('discount_type') not in ['free_shipping']:
            if data.get('discount_value') is None:
                raise serializers.ValidationError(
                    {"discount_value": "Required for this discount type."}
                )
        return data


class CouponDeleteSerializer(serializers.Serializer):
    coupon_id = serializers.CharField()
    

class CouponGetSerializer(serializers.Serializer):
    """Used to validate the coupon_id path param before fetching from Zoho."""
    coupon_id = serializers.CharField()


class CouponUpdateSerializer(serializers.Serializer):
    # All fields optional — only include what you want to change
    coupon_name                 = serializers.CharField(required=False)
    coupon_code                = serializers.CharField(required=False, allow_blank=True)
    description                 = serializers.CharField(required=False, allow_blank=True)
    is_active                   = serializers.BooleanField(required=False)
    show_in_storefront          = serializers.BooleanField(required=False)
    discount_value              = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    max_discount_amount         = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    eligible_product_ids        = serializers.ListField(child=serializers.DictField(), required=False)
    valid_from                  = serializers.CharField(required=False)
    never_expires               = serializers.BooleanField(required=False)
    valid_till                  = serializers.CharField(required=False, allow_blank=True)
    minimum_cart_amount_enabled = serializers.BooleanField(required=False)
    minimum_cart_amount         = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    store_limit_enabled         = serializers.BooleanField(required=False)
    store_limit                 = serializers.IntegerField(required=False)
    customer_limit_enabled      = serializers.BooleanField(required=False)
    customer_limit              = serializers.IntegerField(required=False)