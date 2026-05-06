from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers

from catalog.models import Product, Store
from shop.services.zoho_commerce import ZohoCommerceError, ZohoCommerceService
from zoho_integration.models import ZohoCommerceAccount
from zoho_integration.services import ZohoCommerceService as ZohoAccountService

from .models import (
    Cart,
    CartItem,
    Order,
    OrderItem,
    OrderReturn,
    OrderReturnLine,
    UserAddress,
    WishlistItem,
)


class ProductMiniSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            'id', 'name', 'slug', 'category', 'sku', 'price', 'currency', 'image_url',
        )

    def get_image_url(self, obj):
        current = (getattr(obj, 'image_url', '') or '').strip()
        if current:
            return current
        zoho_pid = (getattr(obj, 'zoho_product_id', '') or '').strip()
        store_id = getattr(obj, 'store_id', None)
        if not (zoho_pid and store_id):
            return ''
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
    store_groups = serializers.SerializerMethodField()
    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = ('cart_id', 'items', 'store_groups', 'subtotal', 'updated_at')

    def get_subtotal(self, obj):
        total = sum((item.line_subtotal for item in obj.items.all()), Decimal('0'))
        return str(total.quantize(Decimal('0.01')))

    def get_store_groups(self, obj):
        items = list(obj.items.all())
        if not items:
            return []
        by_store = {}
        for it in items:
            by_store.setdefault(it.store_id, []).append(it)
        for lines in by_store.values():
            lines.sort(key=lambda x: x.pk)
        def sort_key(sid):
            st = by_store[sid][0].store
            return (st.name.lower(), st.pk)

        groups = []
        for sid in sorted(by_store.keys(), key=sort_key):
            lines = by_store[sid]
            store = lines[0].store
            sub = sum((i.line_subtotal for i in lines), Decimal('0'))
            groups.append({
                'store': StoreTinySerializer(store).data,
                'items': CartItemInGroupSerializer(lines, many=True).data,
                'subtotal': str(sub.quantize(Decimal('0.01'))),
            })
        return groups


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


class UserAddressSerializer(serializers.ModelSerializer):
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


def _completed_returns_total(order: Order) -> Decimal:
    total = Decimal('0')
    for ret in order.returns.filter(status=OrderReturn.Status.COMPLETED).prefetch_related(
        'lines__order_item',
    ):
        for line in ret.lines.all():
            total += line.order_item.unit_price * line.quantity
    return total.quantize(Decimal('0.01'))


class OrderSerializer(serializers.ModelSerializer):
    order_id = serializers.IntegerField(source='id', read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    returned_total = serializers.SerializerMethodField()
    balance_remaining = serializers.SerializerMethodField()
    order_code = serializers.SerializerMethodField()
    display_status = serializers.SerializerMethodField()
    items_count = serializers.SerializerMethodField()
    can_reorder = serializers.SerializerMethodField()
    can_return = serializers.SerializerMethodField()
    return_status = serializers.SerializerMethodField()
    order_date = serializers.SerializerMethodField()
    refunded_amount = serializers.SerializerMethodField()
    net_paid = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            'order_id', 'store', 'status', 'currency', 'payment_method',
            'subtotal', 'vat_percent', 'vat_amount', 'shipping_amount', 'total',
            'order_code', 'display_status', 'items_count',
            'can_reorder', 'can_return', 'return_status', 'order_date',
            'shipping_name', 'shipping_phone', 'shipping_address', 'shipping_city',
            'shipping_state', 'shipping_postal_code', 'shipping_country',
            'billing_same_as_shipping',
            'billing_name', 'billing_phone', 'billing_address', 'billing_city',
            'billing_state', 'billing_postal_code', 'billing_country',
            'zoho_checkout_id', 'zoho_salesorder_id',
            'zoho_sync_error', 'zoho_synced_at',
            'returned_total', 'balance_remaining', 'refunded_amount', 'net_paid',
            'items', 'created_at', 'updated_at',
        )
        read_only_fields = (
            'order_id', 'status', 'subtotal', 'vat_percent', 'vat_amount', 'total',
            'zoho_checkout_id', 'zoho_salesorder_id',
            'zoho_sync_error', 'zoho_synced_at',
            'order_code', 'display_status', 'items_count',
            'can_reorder', 'can_return', 'return_status', 'order_date',
            'returned_total', 'balance_remaining', 'refunded_amount', 'net_paid',
            'created_at', 'updated_at',
        )

    @staticmethod
    def _to_base36(num: int) -> str:
        chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        if num <= 0:
            return '0'
        out = ''
        n = num
        while n:
            n, rem = divmod(n, 36)
            out = chars[rem] + out
        return out

    def get_order_code(self, obj):
        # Stable 6-char code for UI cards, e.g. A1B2C3.
        if obj.zoho_salesorder_id:
            raw = ''.join(ch for ch in str(obj.zoho_salesorder_id).upper() if ch.isalnum())
            if raw:
                return raw[-6:].rjust(6, '0')
        return self._to_base36(int(obj.pk or 0)).rjust(6, '0')

    def get_display_status(self, obj):
        mapping = {
            Order.Status.SYNCED: 'Delivered',
            Order.Status.PENDING_ZOHO_SYNC: 'Pending',
            Order.Status.SYNC_FAILED: 'Pending',
            Order.Status.CANCELLED: 'Cancelled',
        }
        return mapping.get(obj.status, 'Pending')

    def get_items_count(self, obj):
        return int(sum((int(it.quantity or 0) for it in obj.items.all()), 0))

    def get_can_reorder(self, obj):
        return bool(obj.items.exists())

    def get_can_return(self, obj):
        if obj.status != Order.Status.SYNCED:
            return False
        return self._order_return_status(obj) != 'full'

    def _order_return_status(self, obj):
        total = Decimal(str(obj.total or '0'))
        refunded = _completed_returns_total(obj)
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
        return str(_completed_returns_total(obj))

    def get_balance_remaining(self, obj):
        br = (obj.total - _completed_returns_total(obj)).quantize(Decimal('0.01'))
        if br < Decimal('0'):
            br = Decimal('0')
        return str(br)

    def get_refunded_amount(self, obj):
        return self.get_returned_total(obj)

    def get_net_paid(self, obj):
        return self.get_balance_remaining(obj)


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
        return attrs


class OrderReturnLineInputSerializer(serializers.Serializer):
    order_item_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1)


class OrderReturnCreateSerializer(serializers.Serializer):
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
        with transaction.atomic():
            ret = OrderReturn.objects.create(order=order, user=user, note=note)
            for row in lines_data:
                oi = OrderItem.objects.get(pk=row['order_item_id'], order=order)
                OrderReturnLine.objects.create(
                    order_return=ret,
                    order_item=oi,
                    quantity=row['quantity'],
                )
        return ret


class OrderReturnLineReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderReturnLine
        fields = ('id', 'order_item', 'quantity')


class OrderReturnReadSerializer(serializers.ModelSerializer):
    lines = OrderReturnLineReadSerializer(many=True, read_only=True)

    class Meta:
        model = OrderReturn
        fields = (
            'id', 'status', 'zoho_salesreturn_id', 'note', 'lines', 'created_at', 'updated_at',
        )
