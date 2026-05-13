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
    store_id = serializers.IntegerField(min_value=1)
    vat_percent = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=Decimal('0'))
    coupon_code = serializers.CharField(max_length=120, required=False, allow_blank=True, trim_whitespace=True)

    def validate_store_id(self, value):
        get_object_or_404(Store, pk=value, is_active=True)
        return value
