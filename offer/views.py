from decimal import Decimal

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Product, Store

from .models import Coupon
from .serializers import OrderSummaryRequestSerializer, StoreIdQuerySerializer
from .services import (
    _as_decimal,
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

        coupon = None
        if not coupon_code:
            applicable = get_applicable_coupons_for_store(request.user, store)
            auto_coupons = applicable.get('auto_applied_coupons') or []
            first_auto = auto_coupons[0] if isinstance(auto_coupons, list) and auto_coupons else None
            if isinstance(first_auto, dict):
                auto_coupon_id = str(first_auto.get('coupon_id') or '').strip()
                if auto_coupon_id:
                    org_raw = (getattr(store, 'zoho_org_id', '') or getattr(settings, 'ZOHO_COMMERCE_ORGANIZATION_ID', '')).strip()
                    try:
                        org_id = int(org_raw)
                    except Exception:
                        org_id = None
                    coupon_qs = Coupon.objects.filter(coupon_id=auto_coupon_id)
                    if org_id is not None:
                        coupon_qs = coupon_qs.filter(org_id=org_id)
                    coupon = coupon_qs.first()
            if coupon is None:
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

        if coupon is None:
            coupon = get_coupon_for_checkout(store, coupon_code)
        if coupon_code and coupon is None:
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

        bxgy_get_item = None
        if (coupon.coupon_type or '').lower() == 'buyxgety':
            get_products = coupon.get_products if isinstance(coupon.get_products, dict) else {}
            get_product_rows = get_products.get('products', []) if isinstance(get_products, dict) else []
            get_qty = float(get_products.get('quantity') or 1) if isinstance(get_products, dict) else 1.0
            max_count = float(coupon.max_discounted_product_count_per_cart or get_qty)
            max_discount_amount = _as_decimal(coupon.max_discount_amount or '0') if coupon.max_discount_amount else Decimal('0')
            discount = Decimal('0.00')
            for product_row in get_product_rows if isinstance(get_product_rows, list) else []:
                if not isinstance(product_row, dict):
                    continue
                zoho_product_id = str(product_row.get('product_id') or '').strip()
                if not zoho_product_id:
                    continue
                product = Product.objects.filter(store=store, zoho_product_id=zoho_product_id).first()
                if product is None:
                    continue
                get_unit_price = product.price
                get_line_total = (get_unit_price * Decimal(str(max_count))).quantize(Decimal('0.01'))
                discount = (get_line_total * _as_decimal(coupon.discount_value or '0') / Decimal('100')).quantize(Decimal('0.01'))
                if max_discount_amount > Decimal('0'):
                    discount = min(discount, max_discount_amount)
                bxgy_get_item = {
                    'name': product.name,
                    'quantity': int(max_count),
                    'unit_price': str(get_unit_price.quantize(Decimal('0.01'))),
                    'line_total': str(get_line_total.quantize(Decimal('0.01'))),
                    'discount': str(discount.quantize(Decimal('0.01'))),
                    'zoho_product_id': zoho_product_id,
                }
                break
        else:
            discount = calculate_coupon_discount(coupon, cart_items, subtotal, shipping_amount, 'AED')
        final_total = (base_total - discount).quantize(Decimal('0.01'))
        if final_total < Decimal('0'):
            final_total = Decimal('0.00')
        breakdown.append({'label': f'Coupon Discount ({coupon.coupon_code})', 'value': -discount})
        breakdown.append({'label': 'Total', 'value': final_total})
        response_data = {
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
        }
        if bxgy_get_item is not None:
            response_data['bxgy_get_item'] = bxgy_get_item
        return Response(response_data, status=status.HTTP_200_OK)
