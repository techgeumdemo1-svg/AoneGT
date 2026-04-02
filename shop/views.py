from decimal import Decimal

from django.db import transaction
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Cart, CartItem, Order, OrderItem
from .serializers import (
    CartSerializer,
    CartAddItemSerializer,
    CartItemSerializer,
    CartItemUpdateSerializer,
    CheckoutSerializer,
    OrderSerializer,
)


class CartDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get('store_id')
        if not store_id:
            return Response(
                {'detail': 'Query parameter store_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        cart = (
            Cart.objects.filter(user=request.user, store_id=store_id)
            .select_related('store')
            .prefetch_related('items__product')
            .first()
        )
        if not cart:
            return Response(
                {'detail': 'No cart for this store.', 'cart': None},
                status=status.HTTP_200_OK,
            )
        return Response(CartSerializer(cart).data, status=status.HTTP_200_OK)


class CartAddItemAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = CartAddItemSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        store = ser.validated_data['store']
        product = ser.validated_data['product']
        quantity = ser.validated_data['quantity']

        with transaction.atomic():
            cart, _ = Cart.objects.select_for_update().get_or_create(
                user=request.user,
                store=store,
            )
            item, created = CartItem.objects.select_for_update().get_or_create(
                cart=cart,
                product=product,
                defaults={'quantity': quantity},
            )
            if not created:
                item.quantity += quantity
                item.save(update_fields=['quantity'])

        item = CartItem.objects.select_related('product').get(pk=item.pk)
        return Response(CartItemSerializer(item).data, status=status.HTTP_200_OK)


class CartItemDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CartItemSerializer

    def get_queryset(self):
        return CartItem.objects.filter(cart__user=self.request.user).select_related(
            'product', 'cart__store',
        )

    def get_serializer_class(self):
        if self.request.method in ('PATCH', 'PUT'):
            return CartItemUpdateSerializer
        return CartItemSerializer

    def perform_destroy(self, instance):
        cart = instance.cart
        super().perform_destroy(instance)
        if not cart.items.exists():
            cart.delete()


class CheckoutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = CheckoutSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        cart = ser.validated_data['cart']
        store = ser.validated_data['store']
        shipping_amount = ser.validated_data.get('shipping_amount') or Decimal('0')

        items = list(cart.items.select_related('product').all())
        subtotal = sum((it.line_subtotal for it in items), Decimal('0'))
        subtotal = subtotal.quantize(Decimal('0.01'))
        shipping_amount = Decimal(shipping_amount).quantize(Decimal('0.01'))
        total = (subtotal + shipping_amount).quantize(Decimal('0.01'))

        billing_same = ser.validated_data['billing_same_as_shipping']
        ship = {k: ser.validated_data[k] for k in (
            'shipping_name', 'shipping_phone', 'shipping_address', 'shipping_city',
            'shipping_state', 'shipping_postal_code', 'shipping_country',
        )}
        if billing_same:
            bill = {
                'billing_name': ship['shipping_name'],
                'billing_phone': ship['shipping_phone'],
                'billing_address': ship['shipping_address'],
                'billing_city': ship['shipping_city'],
                'billing_state': ship['shipping_state'],
                'billing_postal_code': ship['shipping_postal_code'],
                'billing_country': ship['shipping_country'],
            }
        else:
            bill = {k: ser.validated_data[k] for k in (
                'billing_name', 'billing_phone', 'billing_address', 'billing_city',
                'billing_state', 'billing_postal_code', 'billing_country',
            )}

        currency = items[0].product.currency if items else 'AED'

        with transaction.atomic():
            order = Order.objects.create(
                user=request.user,
                store=store,
                status=Order.Status.PENDING_ZOHO,
                currency=currency,
                subtotal=subtotal,
                shipping_amount=shipping_amount,
                total=total,
                billing_same_as_shipping=billing_same,
                **ship,
                **bill,
            )
            for it in items:
                p = it.product
                line = it.line_subtotal.quantize(Decimal('0.01'))
                OrderItem.objects.create(
                    order=order,
                    product=p,
                    product_name=p.name,
                    sku=p.sku,
                    unit_price=p.price,
                    quantity=it.quantity,
                    line_total=line,
                )
            cart.delete()

        order = Order.objects.prefetch_related('items').get(pk=order.pk)
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)


class OrderListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OrderSerializer

    def get_queryset(self):
        return (
            Order.objects.filter(user=self.request.user)
            .select_related('store')
            .prefetch_related('items')
        )


class OrderDetailAPIView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OrderSerializer

    def get_queryset(self):
        return (
            Order.objects.filter(user=self.request.user)
            .select_related('store')
            .prefetch_related('items')
        )
