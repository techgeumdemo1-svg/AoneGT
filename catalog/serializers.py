from rest_framework import serializers
from .models import Store, Product, Banner


class StoreListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = (
            'id', 'name', 'slug', 'contact_email', 'category', 'description', 'logo_url', 'sort_order',
        )


class ProductListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = (
            'id', 'name', 'slug', 'category', 'sku', 'price', 'compare_at_price',
            'currency', 'image_url',
        )


class ProductDetailSerializer(serializers.ModelSerializer):
    store = StoreListSerializer(read_only=True)

    class Meta:
        model = Product
        fields = (
            'id', 'store', 'name', 'slug', 'category', 'sku', 'description',
            'price', 'compare_at_price', 'currency', 'image_url',
            'created_at', 'updated_at',
        )


class StoreAdminSerializer(serializers.ModelSerializer):
    """Staff-only: create/update stores (Django admin or Bearer token with is_staff)."""

    class Meta:
        model = Store
        fields = (
            'id', 'name', 'slug', 'contact_email', 'category', 'description', 'logo_url', 'is_active',
            'zoho_org_id', 'zoho_store_domain',
            'client_id', 'client_secret', 'refresh_token', 'access_token', 'token_expiry',
            'created_at', 'sort_order',
        )
        read_only_fields = ('id', 'created_at')


class BannerSerializer(serializers.ModelSerializer):
    """Public read — carousel banners."""

    banner_id = serializers.IntegerField(source='id', read_only=True)
    store_id = serializers.IntegerField(source='store_id', read_only=True, allow_null=True)

    class Meta:
        model = Banner
        fields = (
            'banner_id',
            'store_id',
            'title',
            'subtitle',
            'image_url',
            'link_url',
            'sort_order',
        )


class BannerAdminSerializer(serializers.ModelSerializer):
    """Staff-only create/update."""

    banner_id = serializers.IntegerField(source='id', read_only=True)
    store_id = serializers.PrimaryKeyRelatedField(
        queryset=Store.objects.all(),
        source='store',
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Banner
        fields = (
            'banner_id',
            'store_id',
            'title',
            'subtitle',
            'image_url',
            'link_url',
            'sort_order',
            'is_active',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('banner_id', 'created_at', 'updated_at')


class ProductAdminSerializer(serializers.ModelSerializer):
    """Staff-only: create/update products under a store (store set from URL on create)."""

    class Meta:
        model = Product
        fields = (
            'id', 'store', 'name', 'slug', 'category', 'sku', 'description', 'price',
            'compare_at_price', 'currency', 'image_url', 'is_active',
            'zoho_product_id', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'store', 'created_at', 'updated_at')
