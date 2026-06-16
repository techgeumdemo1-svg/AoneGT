from decimal import Decimal

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Product, Store
from shop.models import UserAddress
from shop.services.delivery_zones import get_shipping_fee_breakdown
from shop.services.loyalty_coupons import active_loyalty_coupons_queryset

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
        # VAT percent always from server settings — client value ignored.
        from django.conf import settings as _settings
        _default_vat = getattr(_settings, 'DEFAULT_VAT_PERCENT', '5.00')
        vat_percent = Decimal(str(_default_vat)).quantize(Decimal('0.01'))
        coupon_code = (ser.validated_data.get('coupon_code') or '').strip()
        loyalty_coupon_code = (ser.validated_data.get('loyalty_coupon_code') or '').strip()
        _cart, cart_items, subtotal = get_cart_context(request.user, store)
        # Use serializer-validated payment_method. None/blank = no method selected yet
        # → no COD surcharge applied (surcharge only activates when explicitly 'cash_on_delivery').
        payment_method = (ser.validated_data.get('payment_method') or '').strip()

        # Resolve city: address_id (preferred) → city field (fallback) → empty string
        address_id = ser.validated_data.get('address_id')
        resolved_address = None
        city = ''
        if address_id:
            resolved_address = (
                UserAddress.objects.filter(pk=address_id, user=request.user).first()
            )
            if resolved_address is None:
                return Response(
                    {'detail': 'Address not found or does not belong to you.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            city = (resolved_address.city or '').strip()
        else:
            city = (ser.validated_data.get('city') or request.data.get('city') or '').strip()

        address_details = None
        if resolved_address:
            address_details = {
                'id': resolved_address.pk,
                'full_name': resolved_address.full_name,
                'phone_number': resolved_address.phone_number,
                'address': resolved_address.address,
                'city': resolved_address.city,
                'state': resolved_address.state,
                'address_type': resolved_address.address_type,
            }

        # Get detailed shipping breakdown (delivery_fee + cod_surcharge separately)
        if getattr(settings, 'CHECKOUT_TRUST_CLIENT_SHIPPING', False):
            shipping_info = {
                'total': Decimal('0.00'),
                'delivery_fee': Decimal('0.00'),
                'cod_surcharge': Decimal('0.00'),
                'is_free': False,
                'zone_name': None,
                'estimated_delivery_label': '',
            }
        else:
            shipping_info = get_shipping_fee_breakdown(city, subtotal, payment_method)

        shipping_amount = shipping_info['total']
        delivery_fee = shipping_info['delivery_fee']
        cod_surcharge = shipping_info['cod_surcharge']
        is_free_delivery = shipping_info['is_free']
        zone_name = shipping_info['zone_name']
        estimated_delivery_label = shipping_info['estimated_delivery_label']

        vat_amount = ((subtotal * vat_percent) / Decimal('100')).quantize(Decimal('0.01'))
        base_total = (subtotal + vat_amount + shipping_amount).quantize(Decimal('0.01'))

        product_details = {
            item['name']: {
                'count': item['quantity'],
                'price': float((item['unit_price'] * item['quantity']).quantize(Decimal('0.01')))
            }
            for item in cart_items
            if item.get('name')
        }

        def _build_shipping_breakdown_lines(delivery_fee, cod_surcharge, is_free, shipping_amount):
            """Build shipping-related lines for the breakdown list."""
            lines = []
            if is_free:
                lines.append({'label': 'Delivery (Free)', 'value': Decimal('0.00')})
            elif delivery_fee > 0:
                lines.append({'label': 'Delivery Charge', 'value': delivery_fee})
            if cod_surcharge > 0:
                lines.append({'label': 'COD Surcharge', 'value': cod_surcharge})
            # If no individual lines but there is a shipping total (unknown zone fallback)
            if not lines and shipping_amount > 0:
                lines.append({'label': 'Shipping', 'value': shipping_amount})
            return lines

        shipping_lines = _build_shipping_breakdown_lines(
            delivery_fee, cod_surcharge, is_free_delivery, shipping_amount
        )

        base_breakdown = (
            [{'label': 'Subtotal', 'value': subtotal}]
            + shipping_lines
            + [{'label': f'VAT ({vat_percent}%)', 'value': vat_amount}]
        )

        # Shared shipping + address metadata included in every response path
        shipping_meta = {
            'payment_method': payment_method or None,  # None when not yet selected
            'shipping_amount': shipping_amount,
            'delivery_fee': delivery_fee,
            'cod_surcharge': cod_surcharge,
            'is_free_delivery': is_free_delivery,
            'delivery_zone': zone_name,
            'estimated_delivery_label': estimated_delivery_label,
            'address_details': address_details,  # None if city was passed directly
        }

        has_loyalty_coupons = active_loyalty_coupons_queryset(user=request.user).exists()
        loyalty_coupon_applied = False
        if loyalty_coupon_code:
            loyalty_coupon_applied = active_loyalty_coupons_queryset(user=request.user).filter(
                code__iexact=loyalty_coupon_code,
            ).exists()
        loyalty_meta = {
            'loyalty_coupon_applied': loyalty_coupon_applied,
            'has_loyalty_coupons': has_loyalty_coupons,
            'loyalty_discount': '100.00',  # TODO: replace with real loyalty discount calculation
        }

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
                breakdown = base_breakdown + [{'label': 'Total', 'value': base_total}]
                return Response(
                    {
                        'coupon_applied': False,
                        'valid': True,
                        'subtotal': subtotal,
                        'vat_percent': str(vat_percent),
                        'vat_amount': vat_amount,
                        **shipping_meta,
                        **loyalty_meta,
                        'coupon_discount': Decimal('0.00'),
                        'total': base_total,
                        'breakdown': breakdown,
                        'product_details': product_details,
                    },
                    status=status.HTTP_200_OK,
                )

        if coupon is None:
            coupon = get_coupon_for_checkout(store, coupon_code)
        if coupon_code and coupon is None:
            breakdown = base_breakdown + [{'label': 'Total', 'value': base_total}]
            return Response(
                {
                    'coupon_applied': False,
                    'valid': False,
                    'error': 'Coupon not found',
                    'subtotal': subtotal,
                    'vat_percent': str(vat_percent),
                    'vat_amount': vat_amount,
                    **shipping_meta,
                    **loyalty_meta,
                    'coupon_discount': Decimal('0.00'),
                    'total': base_total,
                    'breakdown': breakdown,
                    'product_details': product_details,
                },
                status=status.HTTP_200_OK,
            )

        allowed, reason = coupon_is_applicable(coupon, request.user, cart_items, subtotal)
        if not allowed:
            breakdown = base_breakdown + [{'label': 'Total', 'value': base_total}]
            return Response(
                {
                    'coupon_applied': False,
                    'valid': False,
                    'error': reason,
                    'subtotal': subtotal,
                    'vat_percent': str(vat_percent),
                    'vat_amount': vat_amount,
                    **shipping_meta,
                    **loyalty_meta,
                    'coupon_discount': Decimal('0.00'),
                    'total': base_total,
                    'breakdown': breakdown,
                    'product_details': product_details,
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

        # FIXED: free_shipping coupon — shipping becomes 0, VAT is on full subtotal,
        # discount is NOT subtracted from the taxable base (it only zeroes the shipping charge).
        is_free_shipping_coupon = (coupon.coupon_type or '').lower() == 'free_shipping'  # FIXED: moved up
        is_bxgy_coupon = (coupon.coupon_type or '').lower() == 'buyxgety'  # FIXED: identify bxgy

        if is_free_shipping_coupon:
            # FIXED: For free_shipping the effective shipping charge is 0.
            # VAT applies to the full product subtotal (no product discount exists).
            # grand_total = subtotal + vat_on_subtotal + 0 (shipping is free).
            effective_shipping = Decimal('0.00')  # FIXED: shipping waived
            vat_amount = (subtotal * vat_percent / Decimal('100')).quantize(Decimal('0.01'))  # FIXED: VAT on full subtotal
            final_total = (subtotal + vat_amount + effective_shipping).quantize(Decimal('0.01'))  # FIXED: no shipping cost
            if final_total < Decimal('0'):
                final_total = Decimal('0.00')
            # FIXED: coupon_discount shown to user = original shipping amount (what was waived)
            shipping_discount_display = discount  # the waived shipping amount
        elif is_bxgy_coupon:
            # FIXED: For buyxgety, the discount ONLY applies to the get-item (Y).
            # The buy-item (X) in the cart still pays full price + full VAT.
            # Do NOT subtract the get-item discount from the cart subtotal when computing VAT.
            # subtotal here = buy-item(s) only (cart items). VAT must be on the full cart subtotal.
            # The get-item's net price is already 0 (or reduced) — its VAT contribution is 0.
            # grand_total = (subtotal + vat_on_subtotal) + (get_item_net = 0) + shipping
            effective_shipping = shipping_amount
            vat_amount = (subtotal * vat_percent / Decimal('100')).quantize(Decimal('0.01'))  # FIXED: VAT on full cart subtotal
            # get-item net cost = get_line_total - discount (e.g. 50 - 50 = 0 for 100% off)
            bxgy_net = Decimal('0.00')
            if bxgy_get_item is not None:
                bxgy_gross = _as_decimal(bxgy_get_item.get('line_total') or '0')
                bxgy_disc = _as_decimal(bxgy_get_item.get('discount') or '0')
                bxgy_net = max(bxgy_gross - bxgy_disc, Decimal('0')).quantize(Decimal('0.01'))  # FIXED: net cost of get-item
            final_total = (subtotal + vat_amount + bxgy_net + effective_shipping).quantize(Decimal('0.01'))  # FIXED
            if final_total < Decimal('0'):
                final_total = Decimal('0.00')
            shipping_discount_display = discount  # the get-item discount amount shown to user
        elif discount > Decimal('0.00'):
            # FIXED: transaction / item — VAT on subtotal after product discount
            effective_shipping = shipping_amount
            taxable_amount = max(subtotal - discount, Decimal('0')).quantize(Decimal('0.01'))  # FIXED: guard against negative
            vat_amount = (taxable_amount * vat_percent / Decimal('100')).quantize(Decimal('0.01'))
            final_total = (taxable_amount + vat_amount + effective_shipping).quantize(Decimal('0.01'))
            if final_total < Decimal('0'):
                final_total = Decimal('0.00')
            shipping_discount_display = discount
        else:
            effective_shipping = shipping_amount
            final_total = base_total  # FIXED: no discount, use pre-calculated base_total unchanged
            shipping_discount_display = Decimal('0.00')

        # FIXED: rebuild shipping breakdown lines using effective_shipping so free-shipping
        # coupon shows Delivery (Free) instead of the original charge.
        if is_free_shipping_coupon:
            effective_shipping_lines = [{'label': 'Delivery (Free)', 'value': Decimal('0.00')}]  # FIXED
        else:
            effective_shipping_lines = shipping_lines  # unchanged for other coupon types

        breakdown = (
            [{'label': 'Subtotal', 'value': subtotal}]
            + [{'label': f'Coupon Discount ({coupon.coupon_code})', 'value': -shipping_discount_display}]
            + effective_shipping_lines  # FIXED: use effective shipping lines
            + [
                {'label': f'VAT ({vat_percent}%)', 'value': vat_amount},
                {'label': 'Total', 'value': final_total},
            ]
        )

        response_data = {
            'coupon_applied': True,
            'valid': True,
            'coupon_code': coupon.coupon_code,
            'coupon_name': coupon.coupon_name,
            'coupon_type': coupon.coupon_type,
            'subtotal': subtotal,
            'vat_percent': str(vat_percent),
            'vat_amount': vat_amount,
            **shipping_meta,
            **loyalty_meta,
            'shipping_amount': effective_shipping,  # FIXED: Decimal('0.00') for free_shipping, not string 'FREE'
            'coupon_discount': shipping_discount_display,
            'total': final_total,
            'breakdown': breakdown,
            'product_details': product_details,
        }
        if bxgy_get_item is not None:
            response_data['bxgy_get_item'] = bxgy_get_item
        return Response(response_data, status=status.HTTP_200_OK)
