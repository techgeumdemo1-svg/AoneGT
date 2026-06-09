from decimal import Decimal
from urllib.parse import quote

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers

from catalog.models import Product, Store
from offer.models import Coupon
from shop.services.delivery_zones import get_shipping_fee
from shop.services.zoho_commerce import ZohoCommerceError, ZohoCommerceService
from zoho_integration.models import ZohoCommerceAccount
from zoho_integration.services import ZohoCommerceService as ZohoAccountService

from .loyalty import min_points_to_redeem
from .models import (
    Cart,
    CartItem,
    FCMDeviceToken,
    Order,
    OrderItem,
    OrderReturn,
    OrderReturnLine,
    PurchasePointsLedger,
    UserAddress,
    UserNotification,
    WishlistItem,
)


def order_code_for_order(obj: Order) -> str:
    """Stable 6-char code for UI (same logic as OrderSerializer.get_order_code)."""
    if obj.zoho_salesorder_id:
        raw = ''.join(ch for ch in str(obj.zoho_salesorder_id).upper() if ch.isalnum())
        if raw:
            return raw[-6:].rjust(6, '0')
    chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    num = int(obj.pk or 0)
    if num <= 0:
        return '0'.rjust(6, '0')
    out = ''
    n = num
    while n:
        n, rem = divmod(n, 36)
        out = chars[rem] + out
    return out.rjust(6, '0')


def return_reason_options_payload():
    """Labels for mobile return flow (screenshots: reason picker)."""
    return [
        {'code': code, 'label': label}
        for code, label in OrderReturn.ReturnReason.choices
    ]


def return_flow_ui_payload():
    """
    How the app wires the return modal (select → reason → POST).
    Cancel is UI-only; confirm is POST when the user finishes the flow.
    """
    return {
        'cancel': {
            'type': 'client_only',
            'description': 'Dismiss return screens; no API request.',
        },
        'confirm_return': {
            'type': 'http',
            'method': 'POST',
            'path_template': '/api/shop/orders/returns/?order_id={order_id}',
            'path_template_alt': '/api/shop/orders/{order_id}/returns/',
            'description': (
                'Call after user selects lines and reason. JSON body: return_reason, '
                'lines[{order_item_id, quantity}], optional note / return_reason_detail.'
            ),
        },
        'item_selection': {
            'method': 'GET',
            'path_template': '/api/shop/orders/{order_id}/',
            'lines_field': 'return_eligible_lines',
            'price_fields': ('unit_price', 'unit_price_display', 'currency', 'line_total_display'),
        },
    }


def _returns_refund_total(order: Order) -> Decimal:
    """
    Sum refund value for submitted returns (pending sync counts for UX until rejected).
    """
    total = Decimal('0')
    active_statuses = (
        OrderReturn.Status.PENDING_ZOHO,
        OrderReturn.Status.SYNCED,
        OrderReturn.Status.COMPLETED,
    )
    for ret in order.returns.filter(status__in=active_statuses).prefetch_related(
        'lines__order_item',
    ):
        for line in ret.lines.all():
            total += line.order_item.unit_price * line.quantity
    return total.quantize(Decimal('0.01'))


class ProductMiniSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    category_id = serializers.CharField(source='zoho_category_id', read_only=True)
    collection_id = serializers.CharField(source='zoho_collection_id', read_only=True)

    class Meta:
        model = Product
        fields = (
            'id',
            'name',
            'slug',
            'category',
            'category_id',
            'collection_id',
            'sku',
            'zoho_product_id',
            'price',
            'currency',
            'image_url',
        )

    @staticmethod
    def _is_usable_image_url(value: str) -> bool:
        raw = (value or '').strip()
        return raw.startswith('http://') or raw.startswith('https://') or raw.startswith('/')

    @staticmethod
    def _extract_image_url_from_zoho_payload(payload: dict) -> str:
        if not isinstance(payload, dict):
            return ''
        source = payload.get('product') if isinstance(payload.get('product'), dict) else payload
        if not isinstance(source, dict):
            return ''
        docs = source.get('documents') if isinstance(source.get('documents'), list) else []
        first_doc = docs[0] if docs and isinstance(docs[0], dict) else {}
        variants = source.get('variants') if isinstance(source.get('variants'), list) else []
        first_variant = variants[0] if variants and isinstance(variants[0], dict) else {}
        variant_docs = first_variant.get('documents') if isinstance(first_variant.get('documents'), list) else []
        first_variant_doc = (
            variant_docs[0] if variant_docs and isinstance(variant_docs[0], dict) else {}
        )
        return str(
            source.get('image_url')
            or source.get('image_name')
            or source.get('image')
            or source.get('image_path')
            or first_doc.get('image_url')
            or first_doc.get('url')
            or first_doc.get('document_url')
            or first_doc.get('download_url')
            or first_variant_doc.get('image_url')
            or first_variant_doc.get('url')
            or first_variant_doc.get('document_url')
            or first_variant_doc.get('download_url')
            or ''
        ).strip()

    @staticmethod
    def _build_zoho_cdn_product_document_url(store_domain: str, payload: dict) -> str:
        domain = (store_domain or '').strip().replace('https://', '').replace('http://', '')
        if not domain or not isinstance(payload, dict):
            return ''
        source = payload.get('product') if isinstance(payload.get('product'), dict) else payload
        if not isinstance(source, dict):
            return ''
        top_document_id = str(
            source.get('document_id')
            or source.get('image_document_id')
            or source.get('image_id')
            or ''
        ).strip()
        if top_document_id:
            return (
                f'https://cdn1.zohoecommerce.com/category-images/{quote(top_document_id)}/800x800'
                f'?storefront_domain={domain}'
            )
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
        # Keep existing direct URLs, but do not preserve our internal image proxy path.
        if (
            self._is_usable_image_url(current)
            and not current.startswith('/api/shop/zoho-products/')
            and '/api/shop/zoho-products/' not in current
        ):
            return current
        zoho_pid = (getattr(obj, 'zoho_product_id', '') or '').strip()
        store_id = getattr(obj, 'store_id', None)
        if not (zoho_pid and store_id):
            return ''
        store = getattr(obj, 'store', None) or Store.objects.filter(pk=store_id).first()
        if store is not None:
            try:
                data = ZohoCommerceService.get_product_detail_storefront(
                    zoho_pid,
                    store=store,
                )
                direct = self._extract_image_url_from_zoho_payload(data)
                if self._is_usable_image_url(direct):
                    return direct
                cdn_url = self._build_zoho_cdn_product_document_url(
                    str(getattr(store, 'zoho_store_domain', '') or ''),
                    data,
                )
                if self._is_usable_image_url(cdn_url):
                    return cdn_url
            except ZohoCommerceError:
                pass
            org_id = str(getattr(store, 'zoho_org_id', '') or '').strip()
            if org_id:
                account = ZohoCommerceAccount.objects.filter(
                    is_active=True,
                    organization_id=org_id,
                ).first()
                if account is not None:
                    try:
                        detail = ZohoAccountService(account).get_product_detail(
                            organization_id=org_id,
                            product_id=str(zoho_pid),
                        )
                        source = (
                            detail.get('product')
                            or detail.get('item')
                            or detail.get('data')
                            or detail
                        )
                        if isinstance(source, dict):
                            direct = self._extract_image_url_from_zoho_payload(source)
                            if self._is_usable_image_url(direct):
                                return direct
                            cdn_url = self._build_zoho_cdn_product_document_url(
                                str(getattr(store, 'zoho_store_domain', '') or ''),
                                source,
                            )
                            if self._is_usable_image_url(cdn_url):
                                return cdn_url
                    except Exception:
                        pass
        request = self.context.get('request')
        path = f'/api/shop/zoho-products/{zoho_pid}/image/?store_id={store_id}'
        return request.build_absolute_uri(path) if request else path


class StoreTinySerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = ('id', 'name', 'slug')


class CartItemSerializer(serializers.ModelSerializer):
    item_id = serializers.IntegerField(source='id', read_only=True)
    store = StoreTinySerializer(read_only=True)
    product = ProductMiniSerializer(read_only=True)
    product_id = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.filter(is_active=True),
        source='product',
        write_only=True,
    )
    line_subtotal = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ('item_id', 'store', 'product', 'product_id', 'quantity', 'line_subtotal')

    def get_line_subtotal(self, obj):
        return str(obj.line_subtotal.quantize(Decimal('0.01')))


class CartItemInGroupSerializer(serializers.ModelSerializer):
    """Line inside a store group (store is omitted; it is on the parent group)."""

    item_id = serializers.IntegerField(source='id', read_only=True)
    product = ProductMiniSerializer(read_only=True)
    line_subtotal = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ('item_id', 'product', 'quantity', 'line_subtotal')

    def get_line_subtotal(self, obj):
        return str(obj.line_subtotal.quantize(Decimal('0.01')))


class CartSerializer(serializers.ModelSerializer):
    cart_id = serializers.IntegerField(source='id', read_only=True)
    items = CartItemSerializer(many=True, read_only=True)
    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = ('cart_id', 'items', 'subtotal', 'updated_at')

    def get_subtotal(self, obj):
        total = sum((item.line_subtotal for item in obj.items.all()), Decimal('0'))
        return str(total.quantize(Decimal('0.01')))

class CartAddFromZohoAccountSerializer(serializers.Serializer):
    """
    Same flow as store-list + product-list under /zoho/multi/... — uses ZohoCommerceAccount id
    and organization_id from the store list, plus zoho_product_id from product list JSON.
    Optional primary_domain from store list (needed to auto-create local Store if missing).
    """

    zoho_account_id = serializers.IntegerField(min_value=1)
    organization_id = serializers.CharField(max_length=120)
    zoho_product_id = serializers.CharField(max_length=120)
    quantity = serializers.IntegerField(min_value=1, default=1)
    primary_domain = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text='From /zoho/multi/stores/ for this organization (e.g. www.example.com).',
    )

    def validate(self, attrs):
        zoho_product_id = (attrs.get('zoho_product_id') or '').strip()
        if not zoho_product_id:
            raise serializers.ValidationError({'zoho_product_id': 'This field is required.'})
        org = (attrs.get('organization_id') or '').strip()
        if not org:
            raise serializers.ValidationError({'organization_id': 'This field is required.'})
        attrs['zoho_product_id'] = zoho_product_id
        attrs['organization_id'] = org
        attrs['primary_domain'] = (attrs.get('primary_domain') or '').strip()
        return attrs


class CartItemUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CartItem
        fields = ('quantity',)
        extra_kwargs = {'quantity': {'min_value': 1}}


class CartItemDeltaSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=('increment', 'decrement'))
    step = serializers.IntegerField(min_value=1, required=False, default=1)


class WishlistStoreSerializer(serializers.ModelSerializer):
    store_id = serializers.IntegerField(source='id', read_only=True)

    class Meta:
        model = Store
        fields = ('store_id', 'name', 'slug')


class WishlistProductSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(source='id', read_only=True)
    image_url = serializers.SerializerMethodField()

    @staticmethod
    def _is_usable_image_url(value: str) -> bool:
        raw = (value or '').strip()
        return raw.startswith('http://') or raw.startswith('https://') or raw.startswith('/')

    @staticmethod
    def _extract_image_url_from_zoho_payload(payload: dict) -> str:
        if not isinstance(payload, dict):
            return ''
        source = payload.get('product') if isinstance(payload.get('product'), dict) else payload
        if not isinstance(source, dict):
            return ''
        docs = source.get('documents') if isinstance(source.get('documents'), list) else []
        first_doc = docs[0] if docs and isinstance(docs[0], dict) else {}
        variants = source.get('variants') if isinstance(source.get('variants'), list) else []
        first_variant = variants[0] if variants and isinstance(variants[0], dict) else {}
        variant_docs = first_variant.get('documents') if isinstance(first_variant.get('documents'), list) else []
        first_variant_doc = (
            variant_docs[0] if variant_docs and isinstance(variant_docs[0], dict) else {}
        )
        return str(
            source.get('image_url')
            or source.get('image')
            or source.get('image_path')
            or first_doc.get('image_url')
            or first_doc.get('url')
            or first_doc.get('document_url')
            or first_doc.get('download_url')
            or first_variant_doc.get('image_url')
            or first_variant_doc.get('url')
            or first_variant_doc.get('document_url')
            or first_variant_doc.get('download_url')
            or ''
        ).strip()

    def get_image_url(self, obj):
        current = (getattr(obj, 'image_url', '') or '').strip()
        if self._is_usable_image_url(current):
            return current
        zoho_pid = (getattr(obj, 'zoho_product_id', '') or '').strip()
        store_id = getattr(obj, 'store_id', None)
        if not (zoho_pid and store_id):
            return ''
        store = getattr(obj, 'store', None) or Store.objects.filter(pk=store_id).first()
        if store is not None:
            try:
                data = ZohoCommerceService.get_product_detail_storefront(
                    zoho_pid,
                    store=store,
                )
                direct = self._extract_image_url_from_zoho_payload(data)
                if self._is_usable_image_url(direct):
                    return direct
            except ZohoCommerceError:
                pass

            # Account-level fallback can expose direct CDN URLs on some orgs.
            org_id = str(getattr(store, 'zoho_org_id', '') or '').strip()
            if org_id:
                account = None
                for row in ZohoCommerceAccount.objects.filter(is_active=True):
                    if str(getattr(row, 'organization_id', '') or '').strip() == org_id:
                        account = row
                        break
                if account is not None:
                    try:
                        detail = ZohoAccountService(account).get_product_detail(
                            organization_id=org_id,
                            product_id=str(zoho_pid),
                        )
                        source = (
                            detail.get('product')
                            or detail.get('item')
                            or detail.get('data')
                            or detail
                        )
                        if isinstance(source, dict):
                            direct = self._extract_image_url_from_zoho_payload(source)
                            if self._is_usable_image_url(direct):
                                return direct
                    except Exception:
                        pass
                    proxy_path = (
                        f'/zoho/multi/accounts/{account.id}/categories/{org_id}/{zoho_pid}/image/'
                    )
                    request = self.context.get('request')
                    return request.build_absolute_uri(proxy_path) if request else proxy_path
            # Fallback that does not require account/org mapping.
            request = self.context.get('request')
            shop_proxy_path = f'/api/shop/zoho-products/{zoho_pid}/image/?store_id={store.id}'
            return request.build_absolute_uri(shop_proxy_path) if request else shop_proxy_path

        placeholder = str(getattr(settings, 'ZOHO_IMAGE_PLACEHOLDER_URL', '') or '').strip()
        return placeholder if self._is_usable_image_url(placeholder) else ''

    class Meta:
        model = Product
        fields = (
            'product_id',
            'zoho_product_id',
            'name',
            'slug',
            'category',
            'sku',
            'price',
            'currency',
            'image_url',
        )


class WishlistItemSerializer(serializers.ModelSerializer):
    wishlist_item_id = serializers.IntegerField(source='id', read_only=True)
    store = WishlistStoreSerializer(read_only=True)
    product = WishlistProductSerializer(read_only=True)

    class Meta:
        model = WishlistItem
        fields = ('wishlist_item_id', 'store', 'product', 'created_at')


class WishlistMoveToCartSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1, required=False, default=1)
    remove_from_wishlist = serializers.BooleanField(required=False, default=True)


class PurchasePointsLedgerSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchasePointsLedger
        fields = ('order_id', 'points_awarded', 'note', 'created_at')
        read_only_fields = fields


class UserAddressSerializer(serializers.ModelSerializer):
    _address_type_aliases = {
        'home': 'home',
        'flat': 'flat',
        'office': 'office',
        'apartments': 'apartments',
    }

    class Meta:
        model = UserAddress
        fields = (
            'id',
            'full_name',
            'phone_number',
            'address',
            'city',
            'state',
            'address_type',
            'is_default',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')

    def validate_full_name(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Full name is required.')
        return value

    def validate_phone_number(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Phone number is required.')
        return value

    def validate_address(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Address is required.')
        return value

    def validate_city(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('City is required.')
        return value

    def validate_state(self, value):
        return (value or '').strip()

    def validate_address_type(self, value):
        normalized = (value or '').strip().lower()
        return self._address_type_aliases.get(normalized, normalized)

    def create(self, validated_data):
        user = self.context['request'].user
        validated_data['user'] = user
        if validated_data.get('is_default'):
            UserAddress.objects.filter(user=user, is_default=True).update(is_default=False)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if validated_data.get('is_default'):
            UserAddress.objects.filter(user=instance.user, is_default=True).exclude(
                pk=instance.pk,
            ).update(is_default=False)
        return super().update(instance, validated_data)


class OrderItemSerializer(serializers.ModelSerializer):
    item_id = serializers.IntegerField(source='id', read_only=True)
    product_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = OrderItem
        fields = (
            'item_id', 'product_id', 'product_name', 'sku',
            'unit_price', 'quantity', 'line_total', 'zoho_line_item_id',
        )


# Mobile order-tracking rail (labels). Per-stage updates require Zoho fulfilment data not stored here yet.
ORDER_CUSTOMER_TRACKING_PIPELINE = (
    ('pending', 'Pending'),
    ('confirmed', 'Confirmed'),
    ('packed', 'Packed'),
    ('out_for_delivery', 'Out for Delivery'),
    ('delivered', 'Delivered'),
)

ORDER_CUSTOMER_TRACKING_STAGE_LABELS = dict(ORDER_CUSTOMER_TRACKING_PIPELINE)


def _tracking_stage_index(stage_key: str) -> int:
    keys = [k for k, _ in ORDER_CUSTOMER_TRACKING_PIPELINE]
    try:
        return keys.index(stage_key)
    except ValueError:
        return 0


def _effective_customer_tracking_stage(order: Order) -> str:
    stage = (getattr(order, 'customer_tracking_stage', '') or '').strip()
    if stage and stage in ORDER_CUSTOMER_TRACKING_STAGE_LABELS:
        return stage
    if order.status == Order.Status.SYNCED:
        return 'confirmed'
    return 'pending'


def order_allows_returns(order: Order) -> bool:
    """Customer may start a return when Zoho sync completed or order marked delivered."""
    if order.status == Order.Status.CANCELLED:
        return False
    if order.status == Order.Status.SYNCED:
        return True
    stage = (getattr(order, 'customer_tracking_stage', '') or '').strip()
    return stage == Order.CustomerTrackingStage.DELIVERED


def order_return_ineligible_message(order: Order) -> str:
    stage = (getattr(order, 'customer_tracking_stage', '') or '').strip() or 'none'
    return (
        "Returns require order status 'synced' or customer tracking 'delivered'. "
        f"Current status={order.status}, tracking_stage={stage}."
    )


class OrderSerializer(serializers.ModelSerializer):
    order_id = serializers.IntegerField(source='id', read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    returned_total = serializers.SerializerMethodField()
    balance_remaining = serializers.SerializerMethodField()
    order_code = serializers.SerializerMethodField()
    display_status = serializers.SerializerMethodField()
    tracking = serializers.SerializerMethodField()
    items_count = serializers.SerializerMethodField()
    can_reorder = serializers.SerializerMethodField()
    can_return = serializers.SerializerMethodField()
    return_status = serializers.SerializerMethodField()
    order_date = serializers.SerializerMethodField()
    refunded_amount = serializers.SerializerMethodField()
    net_paid = serializers.SerializerMethodField()
    return_eligible_lines = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            'order_id', 'store', 'status', 'currency', 'payment_method', 'payment_status',
            'gateway_reference', 'prepaid_credited_amount', 'credit_applied_on_invoice',
            'credit_refunded_remainder',
            'subtotal', 'vat_percent', 'vat_amount', 'shipping_amount', 'total',
            'order_code', 'display_status', 'tracking', 'items_count',
            'can_reorder', 'can_return', 'return_status', 'order_date',
            'return_eligible_lines',
            'shipping_name', 'shipping_phone', 'shipping_address', 'shipping_city',
            'shipping_state', 'shipping_postal_code', 'shipping_country',
            'billing_same_as_shipping',
            'billing_name', 'billing_phone', 'billing_address', 'billing_city',
            'billing_state', 'billing_postal_code', 'billing_country',
            'zoho_checkout_id', 'zoho_salesorder_id',
            'zoho_sync_error', 'zoho_synced_at',
            'zoho_books_invoice_id', 'zoho_books_invoice_number',
            'zoho_books_invoiced_at', 'zoho_books_invoice_error',
            'zoho_books_salesorder_id', 'zoho_books_salesorder_number',
            'zoho_books_salesordered_at', 'zoho_books_salesorder_error',
            'zoho_books_payment_id', 'zoho_books_paid_at', 'zoho_books_payment_error',
            'customer_tracking_stage', 'out_for_delivery_email_sent_at',
            'returned_total', 'balance_remaining', 'refunded_amount', 'net_paid',
            'loyalty_points_redeemed', 'loyalty_discount',
            'items', 'created_at', 'updated_at',
        )
        read_only_fields = (
            'order_id', 'status', 'subtotal', 'vat_percent', 'vat_amount', 'total',
            'payment_status', 'gateway_reference', 'prepaid_credited_amount',
            'credit_applied_on_invoice', 'credit_refunded_remainder',
            'zoho_checkout_id', 'zoho_salesorder_id',
            'zoho_sync_error', 'zoho_synced_at',
            'zoho_books_invoice_id', 'zoho_books_invoice_number',
            'zoho_books_invoiced_at', 'zoho_books_invoice_error',
            'zoho_books_salesorder_id', 'zoho_books_salesorder_number',
            'zoho_books_salesordered_at', 'zoho_books_salesorder_error',
            'zoho_books_payment_id', 'zoho_books_paid_at', 'zoho_books_payment_error',
            'order_code', 'display_status', 'tracking', 'items_count',
            'can_reorder', 'can_return', 'return_status', 'order_date',
            'return_eligible_lines',
            'returned_total', 'balance_remaining', 'refunded_amount', 'net_paid',
            'loyalty_points_redeemed', 'loyalty_discount',
            'created_at', 'updated_at',
        )

    def get_order_code(self, obj):
        return order_code_for_order(obj)

    def get_return_eligible_lines(self, obj):
        """Lines still returnable (for select-items-to-return modal). Empty if order not eligible."""
        if not order_allows_returns(obj):
            return []
        currency = ((obj.currency or '') or 'AED').strip() or 'AED'
        result = []
        for oi in obj.items.all():
            remaining = oi.quantity - oi.quantity_in_active_returns()
            if remaining <= 0:
                continue
            unit = Decimal(str(oi.unit_price)).quantize(Decimal('0.01'))
            lt = Decimal(str(oi.line_total)).quantize(Decimal('0.01'))
            result.append({
                'order_item_id': oi.pk,
                'product_id': oi.product_id,
                'product_name': oi.product_name,
                'sku': oi.sku,
                'currency': currency,
                'unit_price': str(unit),
                'unit_price_display': f'{currency} {unit}',
                'quantity_ordered': oi.quantity,
                'quantity_returnable': remaining,
                'line_total': str(lt),
                'line_total_display': f'{currency} {lt}',
            })
        return result

    def get_display_status(self, obj):
        if obj.status == Order.Status.CANCELLED:
            return 'Cancelled'
        if obj.status in (Order.Status.PENDING_ZOHO_SYNC, Order.Status.SYNC_FAILED):
            return 'Pending'
        if obj.status == Order.Status.SYNCED:
            if self._order_return_status(obj) == 'full':
                return 'Returned'
            stage = _effective_customer_tracking_stage(obj)
            label = ORDER_CUSTOMER_TRACKING_STAGE_LABELS.get(stage)
            if label:
                return label
            return 'Delivered'
        return 'Pending'

    def get_tracking(self, obj):
        def step_dict(key: str, label: str, state: str) -> dict:
            return {'key': key, 'label': label, 'state': state}

        pipeline = ORDER_CUSTOMER_TRACKING_PIPELINE
        if obj.status == Order.Status.CANCELLED:
            return {
                'steps': [step_dict(k, l, 'skipped') for k, l in pipeline],
                'current_key': 'cancelled',
                'current_label': 'Cancelled',
                'is_cancelled': True,
                'is_returned': False,
                'note': 'Order was cancelled.',
            }

        ret = self._order_return_status(obj)
        if ret == 'full':
            steps = [step_dict(k, l, 'completed') for k, l in pipeline]
            steps.append(step_dict('returned', 'Returned', 'current'))
            return {
                'steps': steps,
                'current_key': 'returned',
                'current_label': 'Returned',
                'is_cancelled': False,
                'is_returned': True,
                'note': None,
            }

        if obj.status in (Order.Status.PENDING_ZOHO_SYNC, Order.Status.SYNC_FAILED):
            steps = []
            for i, (k, l) in enumerate(pipeline):
                steps.append(step_dict(k, l, 'current' if i == 0 else 'upcoming'))
            return {
                'steps': steps,
                'current_key': pipeline[0][0],
                'current_label': pipeline[0][1],
                'is_cancelled': False,
                'is_returned': False,
                'note': (
                    'Further stages appear after the order is confirmed.'
                ),
            }

        stage_key = _effective_customer_tracking_stage(obj)
        stage_idx = _tracking_stage_index(stage_key)
        steps = []
        for i, (k, l) in enumerate(pipeline):
            if i < stage_idx:
                state = 'completed'
            elif i == stage_idx:
                state = 'current'
            else:
                state = 'upcoming'
            steps.append(step_dict(k, l, state))
        note = None
        if ret == 'partial':
            note = 'Some items were returned; order is still in progress with partial refund.'
        return {
            'steps': steps,
            'current_key': stage_key,
            'current_label': ORDER_CUSTOMER_TRACKING_STAGE_LABELS.get(stage_key, stage_key),
            'is_cancelled': False,
            'is_returned': False,
            'note': note,
        }

    def get_items_count(self, obj):
        return int(sum((int(it.quantity or 0) for it in obj.items.all()), 0))

    def get_can_reorder(self, obj):
        return bool(obj.items.exists())

    def get_can_return(self, obj):
        if not order_allows_returns(obj):
            return False
        return self._order_return_status(obj) != 'full'

    def _order_return_status(self, obj):
        total = Decimal(str(obj.total or '0'))
        refunded = _returns_refund_total(obj)
        if refunded <= Decimal('0'):
            return 'none'
        if refunded >= total and total > Decimal('0'):
            return 'full'
        return 'partial'

    def get_return_status(self, obj):
        return self._order_return_status(obj)

    def get_order_date(self, obj):
        return obj.created_at.strftime('%d %b %Y')

    def get_returned_total(self, obj):
        return str(_returns_refund_total(obj))

    def get_balance_remaining(self, obj):
        br = (obj.total - _returns_refund_total(obj)).quantize(Decimal('0.01'))
        if br < Decimal('0'):
            br = Decimal('0')
        return str(br)

    def get_refunded_amount(self, obj):
        return self.get_returned_total(obj)

    def get_net_paid(self, obj):
        return self.get_balance_remaining(obj)


class LoyaltyIssueCouponSerializer(serializers.Serializer):
    points = serializers.IntegerField(min_value=1)

    def validate_points(self, value):
        m = min_points_to_redeem()
        if value < m:
            raise serializers.ValidationError(f'At least {m} points are required to issue a coupon.')
        return value


class CheckoutSerializer(serializers.Serializer):
    store_id = serializers.IntegerField()
    address_id = serializers.IntegerField(required=False, min_value=1)
    payment_method = serializers.ChoiceField(
        choices=Order.PaymentMethod.choices,
        required=False,
        default=Order.PaymentMethod.CASH_ON_DELIVERY,
    )
    vat_percent = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        min_value=Decimal('0'),
        default=Decimal('5.00'),
    )
    shipping_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        default=Decimal('0'),
        min_value=Decimal('0'),
    )

    shipping_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    shipping_phone = serializers.CharField(max_length=50, required=False, allow_blank=True)
    shipping_address = serializers.CharField(max_length=500, required=False, allow_blank=True)
    shipping_city = serializers.CharField(max_length=120, required=False, allow_blank=True)
    shipping_state = serializers.CharField(max_length=120, required=False, allow_blank=True)
    shipping_postal_code = serializers.CharField(max_length=32, required=False, allow_blank=True)
    shipping_country = serializers.CharField(max_length=120, required=False, allow_blank=True)

    billing_same_as_shipping = serializers.BooleanField(default=True)
    billing_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    billing_phone = serializers.CharField(max_length=50, required=False, allow_blank=True)
    billing_address = serializers.CharField(max_length=500, required=False, allow_blank=True)
    billing_city = serializers.CharField(max_length=120, required=False, allow_blank=True)
    billing_state = serializers.CharField(max_length=120, required=False, allow_blank=True)
    billing_postal_code = serializers.CharField(max_length=32, required=False, allow_blank=True)
    billing_country = serializers.CharField(max_length=120, required=False, allow_blank=True)

    points_to_redeem = serializers.IntegerField(required=False, default=0, min_value=0)
    loyalty_coupon_code = serializers.CharField(
        max_length=32,
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )
    coupon_code = serializers.CharField(
        max_length=120,
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )
    coupon_discount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        allow_null=True,
        required=False,
        min_value=Decimal('0'),
    )
    payment_success = serializers.BooleanField(
        required=False,
        allow_null=True,
        default=None,
        help_text='Required true for payment_gateway / pay_by_link after gateway confirms payment.',
    )
    gateway_reference = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        help_text='Gateway or pay-by-link transaction id after successful payment.',
    )
    payment_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        min_value=Decimal('0'),
        help_text='Optional; defaults to order total. Must match order total when provided.',
    )

    def validate(self, attrs):
        store = get_object_or_404(Store, pk=attrs['store_id'], is_active=True)
        attrs['store'] = store

        request = self.context.get('request')
        user = request.user if request else None
        cart = (
            Cart.objects.filter(user=user)
            .prefetch_related('items__product', 'items__store')
            .first()
        )
        if not cart:
            raise serializers.ValidationError({'cart': 'No cart found.'})
        checkout_items = [i for i in cart.items.all() if i.store_id == store.pk]
        if not checkout_items:
            raise serializers.ValidationError({'cart': 'Cart has no items for this store.'})
        attrs['cart'] = cart
        attrs['checkout_items'] = checkout_items

        address_id = attrs.get('address_id')
        if address_id:
            address = UserAddress.objects.filter(
                pk=address_id,
                user=request.user if request else None,
            ).first()
            if not address:
                raise serializers.ValidationError({'address_id': 'Address not found.'})
            attrs['shipping_name'] = address.full_name
            attrs['shipping_phone'] = address.phone_number
            attrs['shipping_address'] = address.address
            attrs['shipping_city'] = address.city
            attrs['shipping_state'] = address.state
            attrs['shipping_postal_code'] = attrs.get('shipping_postal_code') or ''
            attrs['shipping_country'] = attrs.get('shipping_country') or 'UAE'
        else:
            default_address = UserAddress.objects.filter(
                user=request.user if request else None,
                is_default=True,
            ).first()
            if default_address:
                attrs['shipping_name'] = default_address.full_name
                attrs['shipping_phone'] = default_address.phone_number
                attrs['shipping_address'] = default_address.address
                attrs['shipping_city'] = default_address.city
                attrs['shipping_state'] = default_address.state
                attrs['shipping_postal_code'] = attrs.get('shipping_postal_code') or ''
                attrs['shipping_country'] = attrs.get('shipping_country') or 'UAE'
            else:
                required_ship = [
                    'shipping_name', 'shipping_phone', 'shipping_address',
                    'shipping_city', 'shipping_country',
                ]
                missing_ship = [f for f in required_ship if not (attrs.get(f) or '').strip()]
                if missing_ship:
                    raise serializers.ValidationError(
                        {f: 'This field is required unless address_id is provided.' for f in missing_ship},
                    )

        # -- Zone-based shipping fee -----------------------------------------
        # Overrides client-supplied shipping_amount when
        # CHECKOUT_TRUST_CLIENT_SHIPPING is False (default).
        # Falls back to DEFAULT_SHIPPING_AMOUNT for unrecognised cities.
        # COD surcharge only applies to cash_on_delivery, NOT to
        # card_on_delivery, pay_by_link, or payment_gateway.
        subtotal = sum((i.line_subtotal for i in checkout_items), Decimal('0')).quantize(Decimal('0.01'))
        if not getattr(settings, 'CHECKOUT_TRUST_CLIENT_SHIPPING', False):
            attrs['shipping_amount'] = get_shipping_fee(
                city=attrs.get('shipping_city', ''),
                subtotal=subtotal,
                payment_method=attrs.get('payment_method', 'cash_on_delivery'),
            )

        if not attrs.get('billing_same_as_shipping'):
            required = [
                'billing_name', 'billing_phone', 'billing_address',
                'billing_city', 'billing_country',
            ]
            missing = [f for f in required if not (attrs.get(f) or '').strip()]
            if missing:
                raise serializers.ValidationError(
                    {f: 'Required when billing is not same as shipping.' for f in missing},
                )

        code = (attrs.get('loyalty_coupon_code') or '').strip()
        pts = int(attrs.get('points_to_redeem') or 0)
        attrs['loyalty_coupon_code'] = code
        attrs['coupon_code'] = (attrs.get('coupon_code') or '').strip()
        coupon_discount = attrs.get('coupon_discount')
        if coupon_discount is not None:
            attrs['coupon_discount'] = Decimal(coupon_discount).quantize(Decimal('0.01'))
        if code and pts > 0:
            raise serializers.ValidationError(
                {'loyalty_coupon_code': 'Use either loyalty coupon code or points_to_redeem, not both.'},
            )

        from shop.services.zoho_books_payment import is_prepaid_at_checkout_payment_method

        payment_method = attrs.get('payment_method', Order.PaymentMethod.CASH_ON_DELIVERY)
        raw_success = attrs.get('payment_success')
        if raw_success is None:
            payment_success = False
        elif isinstance(raw_success, str):
            payment_success = raw_success.strip().lower() in ('true', '1', 'yes')
        else:
            payment_success = bool(raw_success)
        attrs['payment_success'] = payment_success

        gateway_reference = (attrs.get('gateway_reference') or '').strip()
        attrs['gateway_reference'] = gateway_reference

        require_prepaid_payment = getattr(
            settings,
            'CHECKOUT_REQUIRE_PREPAID_PAYMENT_SUCCESS',
            True,
        )

        # payment_gateway: payment has NOT happened yet at checkout time.
        # Payment is initiated after checkout via POST /api/shop/geidea/initiate/
        # and confirmed via the Geidea server-to-server callback.
        # Do NOT require payment_success here for payment_gateway.
        #
        # pay_by_link: same as payment_gateway — order is created first (PENDING),
        # then payment link is generated via POST /api/shop/paybylink/initiate/
        # and confirmed via the Geidea callback. payment_success is not required
        # at checkout for pay_by_link.
        if payment_success and not gateway_reference and payment_method in (
            Order.PaymentMethod.PAY_BY_LINK,
            Order.PaymentMethod.PAYMENT_GATEWAY,
        ):
            raise serializers.ValidationError({
                'gateway_reference': (
                    'Transaction reference is required when payment_success is true.'
                ),
            })

        payment_amount = attrs.get('payment_amount')
        if payment_amount is not None:
            attrs['payment_amount'] = Decimal(payment_amount).quantize(Decimal('0.01'))

        return attrs


class OrderEditSerializer(serializers.Serializer):
    """Edit a pending order before confirm (shipping, billing, payment, shipping fee)."""

    payment_method = serializers.ChoiceField(
        choices=Order.PaymentMethod.choices,
        required=False,
    )
    shipping_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        min_value=Decimal('0'),
    )
    shipping_name = serializers.CharField(max_length=255, required=False)
    shipping_phone = serializers.CharField(max_length=50, required=False)
    shipping_address = serializers.CharField(max_length=500, required=False)
    shipping_city = serializers.CharField(max_length=120, required=False)
    shipping_state = serializers.CharField(max_length=120, required=False, allow_blank=True)
    shipping_postal_code = serializers.CharField(max_length=32, required=False, allow_blank=True)
    shipping_country = serializers.CharField(max_length=120, required=False)
    billing_same_as_shipping = serializers.BooleanField(required=False)
    billing_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    billing_phone = serializers.CharField(max_length=50, required=False, allow_blank=True)
    billing_address = serializers.CharField(max_length=500, required=False, allow_blank=True)
    billing_city = serializers.CharField(max_length=120, required=False, allow_blank=True)
    billing_state = serializers.CharField(max_length=120, required=False, allow_blank=True)
    billing_postal_code = serializers.CharField(max_length=32, required=False, allow_blank=True)
    billing_country = serializers.CharField(max_length=120, required=False, allow_blank=True)


class OrderReturnLineInputSerializer(serializers.Serializer):
    order_item_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1)


class OrderReturnCreateSerializer(serializers.Serializer):
    """Confirm-return step: lines + standardized reason (screenshots 4–6)."""

    return_reason = serializers.ChoiceField(choices=OrderReturn.ReturnReason.choices)
    return_reason_detail = serializers.CharField(
        required=False,
        allow_blank=True,
        default='',
        trim_whitespace=True,
    )
    note = serializers.CharField(required=False, allow_blank=True, default='')
    lines = OrderReturnLineInputSerializer(many=True)

    def validate_lines(self, rows):
        if not rows:
            raise serializers.ValidationError('At least one return line is required.')
        seen = set()
        for row in rows:
            oid = row['order_item_id']
            if oid in seen:
                raise serializers.ValidationError('Duplicate order_item_id in request.')
            seen.add(oid)
        return rows

    def validate(self, attrs):
        order: Order = self.context['order']
        if not order_allows_returns(order):
            raise serializers.ValidationError({'detail': order_return_ineligible_message(order)})
        detail = (attrs.get('return_reason_detail') or '').strip()
        if attrs['return_reason'] == OrderReturn.ReturnReason.OTHER and not detail:
            raise serializers.ValidationError(
                {'return_reason_detail': 'This field is required when return_reason is "other".'},
            )
        attrs['return_reason_detail'] = detail
        for row in attrs['lines']:
            oi = OrderItem.objects.filter(pk=row['order_item_id'], order=order).first()
            if not oi:
                raise serializers.ValidationError(
                    {'lines': f'order_item_id {row["order_item_id"]} is not on this order.'},
                )
            remaining = oi.quantity - oi.quantity_in_active_returns()
            if row['quantity'] > remaining:
                raise serializers.ValidationError(
                    {'lines': f'Quantity exceeds returnable amount for line {oi.pk}.'},
                )
        return attrs

    def create(self, validated_data):
        order = self.context['order']
        user = self.context['request'].user
        lines_data = validated_data['lines']
        note = validated_data.get('note') or ''
        reason = validated_data['return_reason']
        reason_detail = validated_data.get('return_reason_detail') or ''
        with transaction.atomic():
            ret = OrderReturn.objects.create(
                order=order,
                user=user,
                note=note,
                return_reason=reason,
                return_reason_detail=reason_detail,
            )
            for row in lines_data:
                oi = OrderItem.objects.get(pk=row['order_item_id'], order=order)
                OrderReturnLine.objects.create(
                    order_return=ret,
                    order_item=oi,
                    quantity=row['quantity'],
                )
        return ret


class OrderReturnLineReadSerializer(serializers.ModelSerializer):
    order_item = OrderItemSerializer(read_only=True)
    line_subtotal = serializers.SerializerMethodField()
    line_subtotal_display = serializers.SerializerMethodField()

    class Meta:
        model = OrderReturnLine
        fields = (
            'id',
            'order_item',
            'quantity',
            'line_subtotal',
            'line_subtotal_display',
        )
        read_only_fields = (
            'id',
            'order_item',
            'quantity',
            'line_subtotal',
            'line_subtotal_display',
        )

    def get_line_subtotal(self, obj):
        total = Decimal(str(obj.order_item.unit_price)) * int(obj.quantity)
        return str(total.quantize(Decimal('0.01')))

    def get_line_subtotal_display(self, obj):
        order = obj.order_item.order
        cur = ((order.currency or '') or 'AED').strip() or 'AED'
        total = Decimal(str(obj.order_item.unit_price)) * int(obj.quantity)
        amt = total.quantize(Decimal('0.01'))
        return f'{cur} {amt}'


class OrderReturnReadSerializer(serializers.ModelSerializer):
    lines = OrderReturnLineReadSerializer(many=True, read_only=True)
    order_code = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    refund_amount = serializers.SerializerMethodField()
    return_reason_label = serializers.SerializerMethodField()

    class Meta:
        model = OrderReturn
        fields = (
            'id',
            'status',
            'zoho_salesreturn_id',
            'return_reason',
            'return_reason_label',
            'return_reason_detail',
            'note',
            'order_code',
            'currency',
            'refund_amount',
            'lines',
            'created_at',
            'updated_at',
        )

    def get_order_code(self, obj):
        return order_code_for_order(obj.order)

    def get_currency(self, obj):
        return obj.order.currency or 'AED'

    def get_refund_amount(self, obj):
        total = Decimal('0')
        for line in obj.lines.all():
            total += line.order_item.unit_price * line.quantity
        return str(total.quantize(Decimal('0.01')))

    def get_return_reason_label(self, obj):
        raw = (obj.return_reason or '').strip()
        if not raw:
            return ''
        try:
            return OrderReturn.ReturnReason(raw).label
        except ValueError:
            return raw


class UserNotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.SerializerMethodField()

    class Meta:
        model = UserNotification
        fields = (
            'id',
            'kind',
            'title',
            'body',
            'payload',
            'is_read',
            'read_at',
            'created_at',
        )
        read_only_fields = fields

    def get_is_read(self, obj):
        return obj.read_at is not None


class OfferNotificationSerializer(UserNotificationSerializer):
    coupon_name = serializers.SerializerMethodField()
    coupon_code = serializers.SerializerMethodField()
    coupon_description = serializers.SerializerMethodField()
    coupon_created_date = serializers.SerializerMethodField()
    coupon_created_time = serializers.SerializerMethodField()
    coupon_expiry_date = serializers.SerializerMethodField()
    coupon_expiry_time = serializers.SerializerMethodField()

    class Meta(UserNotificationSerializer.Meta):
        fields = UserNotificationSerializer.Meta.fields + (
            'coupon_name',
            'coupon_code',
            'coupon_description',
            'coupon_created_date',
            'coupon_created_time',
            'coupon_expiry_date',
            'coupon_expiry_time',
        )
        read_only_fields = fields

    def _get_coupon(self, obj):
        coupon_id = (obj.payload or {}).get('coupon_id', '')
        if not coupon_id:
            return None
        if not hasattr(obj, '_coupon_cache'):
            obj._coupon_cache = Coupon.objects.filter(coupon_id=coupon_id).first()
        return obj._coupon_cache

    def _to_dubai(self, dt):
        from zoneinfo import ZoneInfo
        if dt is None:
            return None
        if dt.tzinfo is None:
            from django.utils import timezone
            dt = timezone.make_aware(dt, ZoneInfo('Asia/Dubai'))
        return dt.astimezone(ZoneInfo('Asia/Dubai'))

    def get_coupon_name(self, obj):
        coupon = self._get_coupon(obj)
        return coupon.coupon_name if coupon else None

    def get_coupon_code(self, obj):
        coupon = self._get_coupon(obj)
        return coupon.coupon_code if coupon else None

    def get_coupon_description(self, obj):
        coupon = self._get_coupon(obj)
        return coupon.description if coupon else None

    def get_coupon_created_date(self, obj):
        coupon = self._get_coupon(obj)
        if coupon and coupon.created_at:
            local_dt = self._to_dubai(coupon.created_at)
            return local_dt.strftime('%Y-%m-%d') if local_dt else None
        return None

    def get_coupon_created_time(self, obj):
        coupon = self._get_coupon(obj)
        if coupon and coupon.created_at:
            local_dt = self._to_dubai(coupon.created_at)
            return local_dt.strftime('%H:%M:%S') if local_dt else None
        return None

    def get_coupon_expiry_date(self, obj):
        coupon = self._get_coupon(obj)
        if coupon and coupon.expiry_time:
            local_dt = self._to_dubai(coupon.expiry_time)
            return local_dt.strftime('%Y-%m-%d') if local_dt else None
        return None

    def get_coupon_expiry_time(self, obj):
        coupon = self._get_coupon(obj)
        if coupon and coupon.expiry_time:
            local_dt = self._to_dubai(coupon.expiry_time)
            return local_dt.strftime('%H:%M:%S') if local_dt else None
        return None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get('coupon_expiry_date') is None:
            data.pop('coupon_expiry_date', None)
        if data.get('coupon_expiry_time') is None:
            data.pop('coupon_expiry_time', None)
        return data


class FCMDeviceTokenSerializer(serializers.Serializer):
    token = serializers.CharField()
    device_type = serializers.ChoiceField(choices=FCMDeviceToken.DeviceType.choices)

    def validate_token(self, value):
        token = (value or '').strip()
        if not token:
            raise serializers.ValidationError('Token is required.')
        return token


class PushSettingsSerializer(serializers.Serializer):
    push_enabled = serializers.BooleanField()
