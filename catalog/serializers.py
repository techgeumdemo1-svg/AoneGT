from urllib.parse import quote

from rest_framework import serializers

from zoho_integration.models import ZohoCommerceAccount
from zoho_integration.services import ZohoCommerceService as ZohoAccountService
from .models import Store, Product, Banner, ProductReview
from .services.product_reviews import user_has_delivered_purchase
from .text_utils import html_to_plain_text


class StoreListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = (
            'id', 'name', 'slug', 'contact_email', 'category', 'description', 'logo_url', 'sort_order',
        )


class ProductListSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    @staticmethod
    def _is_cdn_url(value: str) -> bool:
        raw = (value or '').strip().lower()
        return raw.startswith('https://cdn1.zohoecommerce.com/')

    @staticmethod
    def _build_zoho_cdn_product_document_url(store_domain: str, payload: dict) -> str:
        domain = (store_domain or '').strip().replace('https://', '').replace('http://', '')
        if not domain or not isinstance(payload, dict):
            return ''
        source = payload.get('product') if isinstance(payload.get('product'), dict) else payload
        if not isinstance(source, dict):
            return ''
        rows = source.get('documents') or source.get('attachments') or source.get('images') or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_document_id = str(
                    row.get('document_id')
                    or row.get('image_document_id')
                    or row.get('image_id')
                    or row.get('id')
                    or ''
                ).strip()
                if row_document_id:
                    return (
                        f'https://cdn1.zohoecommerce.com/category-images/{quote(row_document_id)}/800x800'
                        f'?storefront_domain={domain}'
                    )
        return ''

    def get_image_url(self, obj):
        current = (getattr(obj, 'image_url', '') or '').strip()
        if self._is_cdn_url(current):
            return current

        zoho_pid = (getattr(obj, 'zoho_product_id', '') or '').strip()
        store = getattr(obj, 'store', None)
        if not store or not zoho_pid:
            return ''

        org_id = (getattr(store, 'zoho_org_id', '') or '').strip()
        store_domain = (getattr(store, 'zoho_store_domain', '') or '').strip()
        if not (org_id and store_domain):
            return ''

        account = ZohoCommerceAccount.objects.filter(is_active=True, organization_id=org_id).first()
        if account is None:
            return ''
        try:
            detail = ZohoAccountService(account).get_product_detail(
                organization_id=org_id,
                product_id=zoho_pid,
            )
            cdn = self._build_zoho_cdn_product_document_url(store_domain, detail)
            return cdn or ''
        except Exception:
            return ''

    class Meta:
        model = Product
        fields = (
            'id', 'name', 'slug', 'category', 'sku', 'price', 'compare_at_price',
            'currency', 'image_url',
        )


class ProductDetailSerializer(serializers.ModelSerializer):
    store = StoreListSerializer(read_only=True)
    description = serializers.SerializerMethodField()
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


class ProductReviewCreateSerializer(serializers.ModelSerializer):
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
        return ProductReview.objects.create(
            user=self.context['request'].user,
            product=self.context['product'],
            **validated_data,
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
