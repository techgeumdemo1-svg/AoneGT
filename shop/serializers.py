from decimal import Decimal

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers

from catalog.models import Product, Store

from .models import Cart, CartItem, Order, OrderItem, OrderReturn, OrderReturnLine


class ProductMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = (
            'id', 'name', 'slug', 'category', 'sku', 'price', 'currency', 'image_url',
        )


class StoreTinySerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = ('id', 'name', 'slug')


class CartItemSerializer(serializers.ModelSerializer):
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
        fields = ('id', 'store', 'product', 'product_id', 'quantity', 'line_subtotal')

    def get_line_subtotal(self, obj):
        return str(obj.line_subtotal.quantize(Decimal('0.01')))


class CartItemInGroupSerializer(serializers.ModelSerializer):
    """Line inside a store group (store is omitted; it is on the parent group)."""

    product = ProductMiniSerializer(read_only=True)
    line_subtotal = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ('id', 'product', 'quantity', 'line_subtotal')

    def get_line_subtotal(self, obj):
        return str(obj.line_subtotal.quantize(Decimal('0.01')))


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    store_groups = serializers.SerializerMethodField()
    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = ('id', 'items', 'store_groups', 'subtotal', 'updated_at')

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


class CartAddZohoItemSerializer(serializers.Serializer):
    store_id = serializers.IntegerField()
    zoho_product_id = serializers.CharField(max_length=120)
    quantity = serializers.IntegerField(min_value=1, default=1)

    def validate(self, attrs):
        store = get_object_or_404(Store, pk=attrs['store_id'], is_active=True)
        zoho_product_id = (attrs.get('zoho_product_id') or '').strip()
        if not zoho_product_id:
            raise serializers.ValidationError({'zoho_product_id': 'This field is required.'})
        attrs['store'] = store
        attrs['zoho_product_id'] = zoho_product_id
        return attrs


class CartItemUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CartItem
        fields = ('quantity',)
        extra_kwargs = {'quantity': {'min_value': 1}}


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = (
            'id', 'product', 'product_name', 'sku',
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
    items = OrderItemSerializer(many=True, read_only=True)
    returned_total = serializers.SerializerMethodField()
    balance_remaining = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            'id', 'store', 'status', 'currency', 'subtotal', 'shipping_amount', 'total',
            'shipping_name', 'shipping_phone', 'shipping_address', 'shipping_city',
            'shipping_state', 'shipping_postal_code', 'shipping_country',
            'billing_same_as_shipping',
            'billing_name', 'billing_phone', 'billing_address', 'billing_city',
            'billing_state', 'billing_postal_code', 'billing_country',
            'zoho_checkout_id', 'zoho_salesorder_id',
            'zoho_sync_error', 'zoho_synced_at',
            'returned_total', 'balance_remaining',
            'items', 'created_at', 'updated_at',
        )
        read_only_fields = (
            'status', 'subtotal', 'total', 'zoho_checkout_id', 'zoho_salesorder_id',
            'zoho_sync_error', 'zoho_synced_at',
            'returned_total', 'balance_remaining',
            'created_at', 'updated_at',
        )

    def get_returned_total(self, obj):
        return str(_completed_returns_total(obj))

    def get_balance_remaining(self, obj):
        br = (obj.total - _completed_returns_total(obj)).quantize(Decimal('0.01'))
        if br < Decimal('0'):
            br = Decimal('0')
        return str(br)


class CheckoutSerializer(serializers.Serializer):
    store_id = serializers.IntegerField()
    shipping_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        default=Decimal('0'),
        min_value=Decimal('0'),
    )

    shipping_name = serializers.CharField(max_length=255)
    shipping_phone = serializers.CharField(max_length=50)
    shipping_address = serializers.CharField(max_length=500)
    shipping_city = serializers.CharField(max_length=120)
    shipping_state = serializers.CharField(max_length=120, required=False, allow_blank=True)
    shipping_postal_code = serializers.CharField(max_length=32, required=False, allow_blank=True)
    shipping_country = serializers.CharField(max_length=120)

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
