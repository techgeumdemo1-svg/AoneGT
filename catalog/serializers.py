from rest_framework import serializers
from .models import Store, Product


class StoreListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = (
            'id', 'name', 'slug', 'description', 'logo_url', 'sort_order',
        )


class ProductListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = (
            'id', 'name', 'slug', 'sku', 'price', 'compare_at_price',
            'currency', 'image_url',
        )


class ProductDetailSerializer(serializers.ModelSerializer):
    store = StoreListSerializer(read_only=True)

    class Meta:
        model = Product
        fields = (
            'id', 'store', 'name', 'slug', 'sku', 'description',
            'price', 'compare_at_price', 'currency', 'image_url',
            'created_at', 'updated_at',
        )


class StoreAdminSerializer(serializers.ModelSerializer):
    """Staff-only: create/update stores (Django admin or Bearer token with is_staff)."""

    class Meta:
        model = Store
        fields = (
            'id', 'name', 'slug', 'description', 'logo_url', 'is_active',
            'zoho_site_id', 'sort_order',
        )
        read_only_fields = ('id',)


class ProductAdminSerializer(serializers.ModelSerializer):
    """Staff-only: create/update products under a store (store set from URL on create)."""

    class Meta:
        model = Product
        fields = (
            'id', 'store', 'name', 'slug', 'sku', 'description', 'price',
            'compare_at_price', 'currency', 'image_url', 'is_active',
            'zoho_product_id', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'store', 'created_at', 'updated_at')
