from decimal import Decimal

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers

from catalog.models import Product, Store

from .models import Cart, CartItem, Order, OrderItem, OrderReturn, OrderReturnLine


class ProductMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ('id', 'name', 'slug', 'sku', 'price', 'currency', 'image_url')


class CartItemSerializer(serializers.ModelSerializer):
    product = ProductMiniSerializer(read_only=True)
    product_id = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.filter(is_active=True),
        source='product',
        write_only=True,
    )
    line_subtotal = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ('id', 'product', 'product_id', 'quantity', 'line_subtotal')

    def get_line_subtotal(self, obj):
        return str(obj.line_subtotal.quantize(Decimal('0.01')))


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = ('id', 'store', 'items', 'subtotal', 'updated_at')

    def get_subtotal(self, obj):
        total = sum((item.line_subtotal for item in obj.items.all()), Decimal('0'))
        return str(total.quantize(Decimal('0.01')))


class CartAddItemSerializer(serializers.Serializer):
    store_id = serializers.IntegerField()
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, default=1)

    def validate(self, attrs):
        store = get_object_or_404(Store, pk=attrs['store_id'], is_active=True)
        product = get_object_or_404(
            Product.objects.filter(is_active=True),
            pk=attrs['product_id'],
            store=store,
        )
        attrs['store'] = store
        attrs['product'] = product
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
            'returned_total', 'balance_remaining',
            'items', 'created_at', 'updated_at',
        )
        read_only_fields = (
            'status', 'subtotal', 'total', 'zoho_checkout_id', 'zoho_salesorder_id',
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
            Cart.objects.filter(user=user, store=store)
            .prefetch_related('items__product')
            .first()
        )
        if not cart or not cart.items.exists():
            raise serializers.ValidationError({'cart': 'Cart is empty for this store.'})
        attrs['cart'] = cart

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
