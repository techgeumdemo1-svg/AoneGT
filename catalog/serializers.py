from rest_framework import serializers

from .models import Store, Product, Banner, ProductReview
from .services.product_images import product_display_image_url
from .services.product_reviews import user_can_review_product, user_has_delivered_purchase
from .text_utils import html_to_plain_text


class StoreListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = (
            'id', 'name', 'slug', 'contact_email', 'category', 'description', 'logo_url', 'sort_order',
        )


class ProductListSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    def get_image_url(self, obj):
        return product_display_image_url(obj, allow_zoho_fetch=False)

    class Meta:
        model = Product
        fields = (
            'id', 'name', 'slug', 'category', 'sku', 'price', 'compare_at_price',
            'currency', 'image_url',
        )


class ProductDetailSerializer(serializers.ModelSerializer):
    store = StoreListSerializer(read_only=True)
    description = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    average_rating = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            'id', 'store', 'name', 'slug', 'category', 'sku', 'description',
            'price', 'compare_at_price', 'currency', 'image_url',
            'review_count', 'average_rating',
            'created_at', 'updated_at',
        )

    def get_description(self, obj):
        return html_to_plain_text(obj.description)

    def get_image_url(self, obj):
        return product_display_image_url(obj, allow_zoho_fetch=True)

    def get_review_count(self, obj):
        return obj.reviews.count()

    def get_average_rating(self, obj):
        from django.db.models import Avg

        row = obj.reviews.aggregate(a=Avg('rating'))
        v = row.get('a')
        if v is None:
            return None
        return round(float(v), 2)


class ProductReviewReadSerializer(serializers.ModelSerializer):
    reviewer_display = serializers.SerializerMethodField()

    class Meta:
        model = ProductReview
        fields = (
            'id',
            'reviewer_display',
            'rating',
            'title',
            'body',
            'created_at',
        )
        read_only_fields = fields

    def get_reviewer_display(self, obj):
        u = obj.user
        first = (u.first_name or '').strip()
        last = (u.last_name or '').strip()
        if first:
            return f'{first} {last[:1]}.'.strip() if last else first
        return 'Customer'


class UserReviewedProductSerializer(serializers.ModelSerializer):
    """A product the authenticated user has already reviewed."""

    product_id = serializers.IntegerField(source='product.pk', read_only=True)
    zoho_product_id = serializers.CharField(source='product.zoho_product_id', read_only=True)
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_image_url = serializers.CharField(source='product.image_url', read_only=True)
    store_id = serializers.IntegerField(source='product.store_id', read_only=True)
    store_name = serializers.CharField(source='product.store.name', read_only=True)
    reviewed_at = serializers.DateTimeField(source='created_at', read_only=True)

    class Meta:
        model = ProductReview
        fields = (
            'id',
            'rating',
            'title',
            'body',
            'reviewed_at',
            'product_id',
            'zoho_product_id',
            'product_name',
            'product_image_url',
            'store_id',
            'store_name',
        )
        read_only_fields = fields


class ProductReviewCreateSerializer(serializers.ModelSerializer):
    body = serializers.CharField(max_length=500, allow_blank=True, required=False)

    class Meta:
        model = ProductReview
        fields = ('rating', 'title', 'body')

    def validate_rating(self, value):
        if value is None or int(value) < 1 or int(value) > 5:
            raise serializers.ValidationError('Rating must be between 1 and 5.')
        return int(value)

    def validate(self, attrs):
        request = self.context.get('request')
        product = self.context.get('product')
        if request is None or not request.user.is_authenticated:
            raise serializers.ValidationError({'detail': 'Authentication required.'})
        if product is None:
            raise serializers.ValidationError({'detail': 'Product not found.'})
        if not user_can_review_product(request.user, product):
            if ProductReview.objects.filter(user=request.user, product=product).exists():
                raise serializers.ValidationError(
                    {'detail': 'You have already submitted a review for this product.'},
                )
            if not user_has_delivered_purchase(request.user, product):
                raise serializers.ValidationError(
                    {
                        'detail': (
                            'You can only review this product after it appears on a delivered order '
                            '(order status synced).'
                        ),
                    },
                )
        return attrs

    def create(self, validated_data):
        from django.db import IntegrityError

        try:
            return ProductReview.objects.create(
                user=self.context['request'].user,
                product=self.context['product'],
                **validated_data,
            )
        except IntegrityError:
            raise serializers.ValidationError(
                {'detail': 'You have already submitted a review for this product.'},
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
    store_id = serializers.IntegerField(read_only=True, allow_null=True)

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
