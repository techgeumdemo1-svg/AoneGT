from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import serializers

from catalog.models import Store


class StoreIdQuerySerializer(serializers.Serializer):
    store_id = serializers.IntegerField(min_value=1)

    def validate_store_id(self, value):
        get_object_or_404(Store, pk=value, is_active=True)
        return value


class OrderSummaryRequestSerializer(serializers.Serializer):
    PAYMENT_METHOD_CHOICES = [
        'cash_on_delivery',
        'card_on_delivery',
        'payment_gateway',
        'pay_by_link',
    ]

    store_id = serializers.IntegerField(min_value=1)
    # vat_percent is accepted from client for backward compatibility but ignored server-side.
    # The server always uses DEFAULT_VAT_PERCENT from settings.
    vat_percent = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=Decimal('0'),
        required=False, default=Decimal('5.00'),
    )
    coupon_code = serializers.CharField(max_length=120, required=False, allow_blank=True, trim_whitespace=True)
    loyalty_coupon_code = serializers.CharField(
        max_length=32,
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )
    # Address resolution: provide address_id (preferred) OR city (fallback). Both optional.
    address_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    city = serializers.CharField(max_length=120, required=False, allow_blank=True, trim_whitespace=True)
    # Payment method is optional. When omitted, no payment-specific surcharges are applied.
    payment_method = serializers.ChoiceField(
        choices=PAYMENT_METHOD_CHOICES,
        required=False,
        allow_blank=True,
        allow_null=True,
        default=None,
    )

    def validate_store_id(self, value):
        get_object_or_404(Store, pk=value, is_active=True)
        return value
