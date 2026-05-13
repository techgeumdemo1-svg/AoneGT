from decimal import Decimal

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Store

from .serializers import OrderSummaryRequestSerializer, StoreIdQuerySerializer
from .services import (
    calculate_coupon_discount,
    coupon_is_applicable,
    get_applicable_coupons_for_store,
    get_cart_context,
    get_coupon_for_checkout,
)


class CheckoutCouponsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        ser = StoreIdQuerySerializer(data=request.query_params)
        ser.is_valid(raise_exception=True)
        store = Store.objects.get(pk=ser.validated_data['store_id'], is_active=True)
        return Response(get_applicable_coupons_for_store(request.user, store), status=status.HTTP_200_OK)


class OrderSummaryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = OrderSummaryRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        store = Store.objects.get(pk=ser.validated_data['store_id'], is_active=True)
        vat_percent = Decimal(ser.validated_data['vat_percent']).quantize(Decimal('0.01'))
        coupon_code = (ser.validated_data.get('coupon_code') or '').strip()
        _cart, cart_items, subtotal = get_cart_context(request.user, store)
        shipping_amount = Decimal(getattr(settings, 'DEFAULT_SHIPPING_AMOUNT', Decimal('0'))).quantize(Decimal('0.01'))
        if getattr(settings, 'CHECKOUT_TRUST_CLIENT_SHIPPING', False):
            shipping_amount = Decimal('0.00')
        vat_amount = ((subtotal * vat_percent) / Decimal('100')).quantize(Decimal('0.01'))
        base_total = (subtotal + vat_amount + shipping_amount).quantize(Decimal('0.01'))
        breakdown = [
            {'label': 'Subtotal', 'value': subtotal},
            {'label': f'VAT ({vat_percent})', 'value': vat_amount},
            {'label': 'Shipping', 'value': shipping_amount},
        ]

        if not coupon_code:
            breakdown.append({'label': 'Total', 'value': base_total})
            return Response(
                {
                    'coupon_applied': False,
                    'valid': True,
                    'subtotal': subtotal,
                    'vat_percent': str(vat_percent),
                    'vat_amount': vat_amount,
                    'shipping_amount': shipping_amount,
                    'coupon_discount': Decimal('0.00'),
                    'total': base_total,
                    'breakdown': breakdown,
                },
                status=status.HTTP_200_OK,
            )

        coupon = get_coupon_for_checkout(store, coupon_code)
        if coupon is None:
            return Response(
                {
                    'coupon_applied': False,
                    'valid': False,
                    'error': 'Coupon not found',
                    'subtotal': subtotal,
                    'vat_percent': str(vat_percent),
                    'vat_amount': vat_amount,
                    'shipping_amount': shipping_amount,
                    'coupon_discount': Decimal('0.00'),
                    'total': base_total,
                    'breakdown': breakdown + [{'label': 'Total', 'value': base_total}],
                },
                status=status.HTTP_200_OK,
            )

        allowed, reason = coupon_is_applicable(coupon, request.user, cart_items, subtotal)
        if not allowed:
            return Response(
                {
                    'coupon_applied': False,
                    'valid': False,
                    'error': reason,
                    'subtotal': subtotal,
                    'vat_percent': str(vat_percent),
                    'vat_amount': vat_amount,
                    'shipping_amount': shipping_amount,
                    'coupon_discount': Decimal('0.00'),
                    'total': base_total,
                    'breakdown': breakdown + [{'label': 'Total', 'value': base_total}],
                },
                status=status.HTTP_200_OK,
            )

        discount = calculate_coupon_discount(coupon, cart_items, subtotal, shipping_amount, 'AED')
        final_total = (base_total - discount).quantize(Decimal('0.01'))
        if final_total < Decimal('0'):
            final_total = Decimal('0.00')
        breakdown.append({'label': f'Coupon Discount ({coupon.coupon_code})', 'value': -discount})
        breakdown.append({'label': 'Total', 'value': final_total})
        return Response(
            {
                'coupon_applied': True,
                'valid': True,
                'coupon_code': coupon.coupon_code,
                'coupon_name': coupon.coupon_name,
                'coupon_type': coupon.coupon_type,
                'subtotal': subtotal,
                'vat_percent': str(vat_percent),
                'vat_amount': vat_amount,
                'shipping_amount': 'FREE' if (coupon.coupon_type or '').lower() == 'free_shipping' else shipping_amount,
                'coupon_discount': discount,
                'total': final_total,
                'breakdown': breakdown,
            },
            status=status.HTTP_200_OK,
        )
