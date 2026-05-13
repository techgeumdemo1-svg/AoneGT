# BXGY Checkout and Summary Report

## 1. Checkout entry point

**File:** [shop/views.py](shop/views.py)

**Class / method:** `CheckoutAPIView.post`

**Line range:** `1147-1434`

**Verbatim method:**

```python
class CheckoutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = CheckoutSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        cart = ser.validated_data['cart']
        store = ser.validated_data['store']
        items = list(ser.validated_data['checkout_items'])
        points_to_redeem_req = int(ser.validated_data.get('points_to_redeem') or 0)
        coupon_code_in = (ser.validated_data.get('loyalty_coupon_code') or '').strip()
        offer_coupon_code = (ser.validated_data.get('coupon_code') or '').strip()
        offer_coupon_discount = ser.validated_data.get('coupon_discount')

        if getattr(settings, 'CHECKOUT_TRUST_CLIENT_SHIPPING', False):
            shipping_amount = ser.validated_data.get('shipping_amount') or Decimal('0')
            shipping_amount = Decimal(shipping_amount).quantize(Decimal('0.01'))
        else:
            shipping_amount = Decimal(settings.DEFAULT_SHIPPING_AMOUNT).quantize(Decimal('0.01'))
        subtotal = sum((it.line_subtotal for it in items), Decimal('0'))
        subtotal = subtotal.quantize(Decimal('0.01'))
        vat_percent = Decimal(ser.validated_data.get('vat_percent') or '0').quantize(Decimal('0.01'))
        vat_amount = ((subtotal * vat_percent) / Decimal('100')).quantize(Decimal('0.01'))
        gross_total = (subtotal + vat_amount + shipping_amount).quantize(Decimal('0.01'))

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
        pv = point_value_aed()
        loyalty_discount = Decimal('0')
        loyalty_points_redeemed = 0
        coupon_row = None
        points_awarded = 0
        offer_coupon = None
        live_redemption = 0
        offer_coupon_discount_value = Decimal('0')

        with transaction.atomic():
            locked_user = User.objects.select_for_update().get(pk=request.user.pk)

            if coupon_code_in:
                coupon_row = (
                    LoyaltyIssuedCoupon.objects.select_for_update()
                    .filter(
                        user_id=locked_user.pk,
                        code__iexact=coupon_code_in,
                        used_at__isnull=True,
                    )
                    .first()
                )
                if not coupon_row:
                    return Response(
                        {'detail': 'Invalid or already used loyalty coupon code.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if coupon_row.expires_at < timezone.now():
                    return Response(
                        {'detail': 'This loyalty coupon has expired.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                loyalty_discount = min(Decimal(coupon_row.amount_aed), gross_total).quantize(Decimal('0.01'))

            elif points_to_redeem_req > 0:
                bal = int(locked_user.points_balance or 0)
                min_w = min_points_to_redeem()
                if bal < min_w:
                    return Response(
                        {
                            'detail': (
                                f'You need at least {min_w} points in your wallet before redeeming.'
                            ),
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                max_pts = max_points_redeemable_for_total(gross_total, pv)
                actual_pts = min(points_to_redeem_req, bal, max_pts)
                if actual_pts <= 0:
                    return Response(
                        {'detail': 'No loyalty points can be applied to this order total.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                discount_calc = (Decimal(actual_pts) * pv).quantize(Decimal('0.01'))
                loyalty_discount = min(discount_calc, gross_total).quantize(Decimal('0.01'))
                loyalty_points_redeemed = actual_pts
                locked_user.points_balance = bal - actual_pts
                locked_user.save(update_fields=['points_balance'])

            if offer_coupon_code:
                offer_coupon = get_coupon_for_checkout(store, offer_coupon_code)
                if offer_coupon is None:
                    return Response({'error': 'Coupon not found'}, status=status.HTTP_400_BAD_REQUEST)
                local_redemption = int(offer_coupon.redemption_count or 0)
                local_max = int(offer_coupon.max_redemption_count or 0)
                if local_max > 0 and local_redemption >= local_max:
                    return Response(
                        {'error': 'Sorry, this coupon is no longer available. Please place your order without it.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                cart_snapshots = [
                    {
                        'product_id': str(getattr(it.product, 'zoho_product_id', '') or ''),
                        'category_id': str(getattr(it.product, 'zoho_category_id', '') or ''),
                        'collection_id': str(getattr(it.product, 'zoho_collection_id', '') or ''),
                        'quantity': int(it.quantity or 0),
                        'line_total': _as_decimal(it.line_subtotal),
                    }
                    for it in items
                ]
                allowed, reason = coupon_is_applicable(offer_coupon, locked_user, cart_snapshots, subtotal)
                if not allowed:
                    return Response({'error': reason}, status=status.HTTP_400_BAD_REQUEST)
                if offer_coupon_discount is not None:
                    offer_coupon_discount_value = Decimal(str(offer_coupon_discount)).quantize(Decimal('0.01'))

            final_total = (gross_total - loyalty_discount).quantize(Decimal('0.01'))
            if offer_coupon is not None:
                final_total = (final_total - offer_coupon_discount_value).quantize(Decimal('0.01'))
            if final_total < 0:
                final_total = Decimal('0')

            order = Order.objects.create(
                user=request.user,
                store=store,
                status=Order.Status.PENDING_ZOHO_SYNC,
                currency=currency,
                payment_method=ser.validated_data['payment_method'],
                subtotal=subtotal,
                vat_percent=vat_percent,
                vat_amount=vat_amount,
                shipping_amount=shipping_amount,
                total=final_total,
                loyalty_points_redeemed=loyalty_points_redeemed,
                loyalty_discount=loyalty_discount,
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
            CartItem.objects.filter(pk__in=[i.pk for i in items]).delete()

            if offer_coupon is not None:
                try:
                    increment_coupon_usage(
                        offer_coupon,
                        order_id=order.pk,
                        user_id=request.user.pk,
                        discount_amount=offer_coupon_discount_value,
                    )
                except Exception:
                    pass

            if coupon_row:
                coupon_row.used_at = timezone.now()
                coupon_row.order = order
                coupon_row.save(update_fields=['used_at', 'order'])

            points_awarded = points_earned_for_purchase(final_total, currency)
            if points_awarded > 0:
                step = aed_per_point_earned()
                PurchasePointsLedger.objects.create(
                    user=request.user,
                    order=order,
                    points_awarded=points_awarded,
                    note=(
                        f'Earned {points_awarded} pt(s): 1 pt per {step} AED of paid total '
                        f'(after loyalty discount).'
                    ),
                )
                uearn = User.objects.select_for_update().get(pk=request.user.pk)
                uearn.points_balance = int(uearn.points_balance or 0) + points_awarded
                uearn.save(update_fields=['points_balance'])

        order = Order.objects.prefetch_related(
            'items', 'returns__lines__order_item',
        ).get(pk=order.pk)
        code = order_code_for_order(order)
        create_user_notification(
            request.user,
            UserNotification.Kind.ORDER,
            title=f'Order #{code} placed',
            body=(
                f'We received your order ({order.currency} {order.total}). '
                'We will update you when it ships.'
            ),
            payload={
                'event': 'order_placed',
                'order_id': order.pk,
                'store_id': order.store_id,
                'order_code': code,
            },
        )
        if points_awarded > 0:
            create_user_notification(
                request.user,
                UserNotification.Kind.POINTS_REWARD,
                title=f'You earned {points_awarded} points',
                body='Points were added to your wallet from this purchase.',
                payload={
                    'event': 'points_earned',
                    'points': points_awarded,
                    'order_id': order.pk,
                },
            )
        if loyalty_points_redeemed > 0:
            create_user_notification(
                request.user,
                UserNotification.Kind.POINTS_DEDUCTED,
                title=f'{loyalty_points_redeemed} points applied',
                body='Loyalty points were redeemed on this order.',
                payload={
                    'event': 'points_redeemed_checkout',
                    'points': loyalty_points_redeemed,
                    'order_id': order.pk,
                },
            )
        selected_payment_method = {
            'code': order.payment_method,
            'label': Order.PaymentMethod(order.payment_method).label,
            'selected': True,
        }
        order_lines = [
            {
                'name': item.product_name,
                'quantity': item.quantity,
                'line_total': str(item.line_total.quantize(Decimal('0.01'))),
            }
            for item in order.items.all()
        ]
        response_payload = {
            'order': OrderSerializer(order).data,
            'checkout_view': {
                'delivery_address': {
                    'name': order.shipping_name,
                    'phone': order.shipping_phone,
                    'address_line': order.shipping_address,
                    'city': order.shipping_city,
                    'state': order.shipping_state,
                    'country': order.shipping_country,
                },
                'payment_methods': [selected_payment_method],
                'order_summary': {
                    'items': order_lines,
                    'subtotal': str(order.subtotal.quantize(Decimal('0.01'))),
                    'vat_percent': str(order.vat_percent.quantize(Decimal('0.01'))),
                    'vat_amount': str(order.vat_amount.quantize(Decimal('0.01'))),
                    'shipping_amount': str(order.shipping_amount.quantize(Decimal('0.01'))),
                    'gross_total': str(gross_total.quantize(Decimal('0.01'))),
                    'loyalty_discount': str(order.loyalty_discount.quantize(Decimal('0.01'))),
                    'points_redeemed': order.loyalty_points_redeemed,
                    'points_earned': points_awarded,
                    'total': str(order.total.quantize(Decimal('0.01'))),
                    'currency': order.currency,
                },
            },
        }
        return Response(response_payload, status=status.HTTP_201_CREATED)
```

**How it currently reads `coupon_code` from the request body:**

```python
offer_coupon_code = (ser.validated_data.get('coupon_code') or '').strip()
```

**How it currently calculates / applies `coupon_discount`:**

```python
if offer_coupon_discount is not None:
    offer_coupon_discount_value = Decimal(str(offer_coupon_discount)).quantize(Decimal('0.01'))
...
if offer_coupon is not None:
    final_total = (final_total - offer_coupon_discount_value).quantize(Decimal('0.01'))
```

**Where it builds the order_summary response:**

```python
order_lines = [
    {
        'name': item.product_name,
        'quantity': item.quantity,
        'line_total': str(item.line_total.quantize(Decimal('0.01'))),
    }
    for item in order.items.all()
]
response_payload = {
    'order': OrderSerializer(order).data,
    'checkout_view': {
        'delivery_address': {
            'name': order.shipping_name,
            'phone': order.shipping_phone,
            'address_line': order.shipping_address,
            'city': order.shipping_city,
            'state': order.shipping_state,
            'country': order.shipping_country,
        },
        'payment_methods': [selected_payment_method],
        'order_summary': {
            'items': order_lines,
            'subtotal': str(order.subtotal.quantize(Decimal('0.01'))),
            'vat_percent': str(order.vat_percent.quantize(Decimal('0.01'))),
            'vat_amount': str(order.vat_amount.quantize(Decimal('0.01'))),
            'shipping_amount': str(order.shipping_amount.quantize(Decimal('0.01'))),
            'gross_total': str(gross_total.quantize(Decimal('0.01'))),
            'loyalty_discount': str(order.loyalty_discount.quantize(Decimal('0.01'))),
            'points_redeemed': order.loyalty_points_redeemed,
            'points_earned': points_awarded,
            'total': str(order.total.quantize(Decimal('0.01'))),
            'currency': order.currency,
        },
    },
}
```

---

## 2. Coupon discount calculation

**File:** [offer/services.py](offer/services.py)

**Function:** `calculate_coupon_discount`

**Line range:** `209-270`

**Verbatim function:**

```python
def calculate_coupon_discount(coupon: Coupon, cart_items: list[dict[str, Any]], subtotal: Decimal, shipping_amount: Decimal, currency: str) -> Decimal:
    discount_amounts = coupon.discount_amounts if isinstance(coupon.discount_amounts, list) else []
    max_discount_amount = _as_decimal(coupon.max_discount_amount or '0') if coupon.max_discount_amount else Decimal('0')
    coupon_type = (coupon.coupon_type or '').lower()
    discount_type = (coupon.discount_type or '').lower()

    def _discount_from_amounts() -> Decimal:
        currency_code = (currency or '').strip().upper()
        for row in discount_amounts:
            if isinstance(row, dict):
                row_currency = str(row.get('currency') or row.get('currency_code') or row.get('code') or '').strip().upper()
                if currency_code and row_currency == currency_code:
                    value = row.get('discount_value') or row.get('amount') or row.get('value') or row.get('discount_amount')
                    if value not in (None, ''):
                        return _as_decimal(value)
        for row in discount_amounts:
            if isinstance(row, dict):
                value = row.get('discount_value') or row.get('amount') or row.get('value') or row.get('discount_amount')
                if value not in (None, ''):
                    return _as_decimal(value)
            elif row not in (None, ''):
                return _as_decimal(row)
        return Decimal('0.00')

    if coupon_type == 'free_shipping':
        return shipping_amount.quantize(Decimal('0.01'))

    if coupon_type == 'transaction':
        if discount_type == 'percentage':
            discount = (subtotal * _as_decimal(coupon.discount_value or '0') / Decimal('100')).quantize(Decimal('0.01'))
            if max_discount_amount > Decimal('0'):
                discount = min(discount, max_discount_amount)
            return discount.quantize(Decimal('0.01'))
        return _discount_from_amounts().quantize(Decimal('0.01'))

    if coupon_type == 'item':
        eligible_products = _json_dict(coupon.eligible_products)
        eligible_total = _matched_line_total(
            cart_items,
            product_ids=_json_list(eligible_products.get('products')),
            categories=_json_list(eligible_products.get('categories')),
            collections=_json_list(eligible_products.get('collections')),
        )
        if discount_type == 'percentage':
            discount = (eligible_total * _as_decimal(coupon.discount_value or '0') / Decimal('100')).quantize(Decimal('0.01'))
            if max_discount_amount > Decimal('0'):
                discount = min(discount, max_discount_amount)
            return discount.quantize(Decimal('0.01'))
        return _discount_from_amounts().quantize(Decimal('0.01'))

    if coupon_type == 'buyxgety':
        get_products = _json_dict(coupon.get_products)
        eligible_total = _matched_line_total(cart_items, product_ids=_json_list(get_products.get('products')))
        if discount_type == 'percentage':
            discount = (eligible_total * _as_decimal(coupon.discount_value or '0') / Decimal('100')).quantize(Decimal('0.01'))
            if max_discount_amount > Decimal('0'):
                discount = min(discount, max_discount_amount)
            return discount.quantize(Decimal('0.01'))
        return _discount_from_amounts().quantize(Decimal('0.01'))

    return Decimal('0.00')
```

**BXGY handling:** Yes, it has special handling for `coupon_type == 'buyxgety'`.

**Those lines are:**

```python
if coupon_type == 'buyxgety':
    get_products = _json_dict(coupon.get_products)
    eligible_total = _matched_line_total(cart_items, product_ids=_json_list(get_products.get('products')))
    if discount_type == 'percentage':
        discount = (eligible_total * _as_decimal(coupon.discount_value or '0') / Decimal('100')).quantize(Decimal('0.01'))
        if max_discount_amount > Decimal('0'):
            discount = min(discount, max_discount_amount)
        return discount.quantize(Decimal('0.01'))
    return _discount_from_amounts().quantize(Decimal('0.01'))
```

**Non-BXGY discount computation:**

- `transaction` coupons:

```python
if coupon_type == 'transaction':
    if discount_type == 'percentage':
        discount = (subtotal * _as_decimal(coupon.discount_value or '0') / Decimal('100')).quantize(Decimal('0.01'))
        if max_discount_amount > Decimal('0'):
            discount = min(discount, max_discount_amount)
        return discount.quantize(Decimal('0.01'))
    return _discount_from_amounts().quantize(Decimal('0.01'))
```

- `item` coupons:

```python
if coupon_type == 'item':
    eligible_products = _json_dict(coupon.eligible_products)
    eligible_total = _matched_line_total(
        cart_items,
        product_ids=_json_list(eligible_products.get('products')),
        categories=_json_list(eligible_products.get('categories')),
        collections=_json_list(eligible_products.get('collections')),
    )
    if discount_type == 'percentage':
        discount = (eligible_total * _as_decimal(coupon.discount_value or '0') / Decimal('100')).quantize(Decimal('0.01'))
        if max_discount_amount > Decimal('0'):
            discount = min(discount, max_discount_amount)
        return discount.quantize(Decimal('0.01'))
    return _discount_from_amounts().quantize(Decimal('0.01'))
```

---

## 3. Order creation

**File:** [shop/views.py](shop/views.py)

**Function / method:** `CheckoutAPIView.post`

**Line range:** `1147-1434`

**Cart items to order line items:**

```python
order = Order.objects.create(
    user=request.user,
    store=store,
    status=Order.Status.PENDING_ZOHO_SYNC,
    currency=currency,
    payment_method=ser.validated_data['payment_method'],
    subtotal=subtotal,
    vat_percent=vat_percent,
    vat_amount=vat_amount,
    shipping_amount=shipping_amount,
    total=final_total,
    loyalty_points_redeemed=loyalty_points_redeemed,
    loyalty_discount=loyalty_discount,
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
CartItem.objects.filter(pk__in=[i.pk for i in items]).delete()
```

**Existing logic that adds extra products beyond the cart:**

No. This method only iterates through `items` and creates one `OrderItem` per cart item. There is no extra-product insertion logic here for `buyxgety`.

**Does it do anything special for `buyxgety`?**

No special line-item creation logic for `buyxgety`. The checkout flow only validates the coupon, subtracts `offer_coupon_discount_value` from `final_total`, and creates order items from the cart.

---

## 4. Order summary builder

**File:** [shop/views.py](shop/views.py)

**Function / method:** `CheckoutAPIView.post`

**Line range:** `1147-1434`

**Items list in order summary:**

```python
order_lines = [
    {
        'name': item.product_name,
        'quantity': item.quantity,
        'line_total': str(item.line_total.quantize(Decimal('0.01'))),
    }
    for item in order.items.all()
]
```

**Breakdown list used in the checkout summary response:**

```python
response_payload = {
    'order': OrderSerializer(order).data,
    'checkout_view': {
        'delivery_address': {
            'name': order.shipping_name,
            'phone': order.shipping_phone,
            'address_line': order.shipping_address,
            'city': order.shipping_city,
            'state': order.shipping_state,
            'country': order.shipping_country,
        },
        'payment_methods': [selected_payment_method],
        'order_summary': {
            'items': order_lines,
            'subtotal': str(order.subtotal.quantize(Decimal('0.01'))),
            'vat_percent': str(order.vat_percent.quantize(Decimal('0.01'))),
            'vat_amount': str(order.vat_amount.quantize(Decimal('0.01'))),
            'shipping_amount': str(order.shipping_amount.quantize(Decimal('0.01'))),
            'gross_total': str(gross_total.quantize(Decimal('0.01'))),
            'loyalty_discount': str(order.loyalty_discount.quantize(Decimal('0.01'))),
            'points_redeemed': order.loyalty_points_redeemed,
            'points_earned': points_awarded,
            'total': str(order.total.quantize(Decimal('0.01'))),
            'currency': order.currency,
        },
    },
}
```

**How the coupon discount line is currently added to breakdown:**

In `OrderSummaryAPIView`, the breakdown uses a single total discount line, not per-item discount lines:

```python
breakdown.append({'label': f'Coupon Discount ({coupon.coupon_code})', 'value': -discount})
breakdown.append({'label': 'Total', 'value': final_total})
```

**Does it show per-item discount or just a total discount?**

Just a total discount.

---

## 5. Product lookup

**Model:** [catalog/models.py](catalog/models.py)

**Relevant fields:**

```python
class Product(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    category = models.CharField(max_length=255, blank=True)
    sku = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    compare_at_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
    )
    currency = models.CharField(max_length=8, default='AED')
    image_url = models.URLField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    zoho_product_id = models.CharField(max_length=120, blank=True)
    zoho_category_id = models.CharField(
        max_length=120,
        blank=True,
        help_text='Zoho Commerce category id when known (from product sync/detail).',
    )
    zoho_collection_id = models.CharField(
        max_length=120,
        blank=True,
        help_text='Zoho Commerce collection id when present on product payload.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

**Exact query used to fetch a product by `zoho_product_id`:**

**File:** [shop/views.py](shop/views.py)

**Line:** `218`

```python
product = Product.objects.filter(store=store, zoho_product_id=zoho_product_id).first()
```

**What fields are available on the product model?**

The fields relevant to this lookup are `name`, `price`, `zoho_product_id`, and `currency`, plus the related Zoho fields `zoho_category_id` and `zoho_collection_id` shown above.

---

## 6. order-summary endpoint

**File:** [offer/views.py](offer/views.py)

**Function / method:** `OrderSummaryAPIView.post`

**Line range:** `31-87`

**Verbatim function:**

```python
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
```

**How it handles `coupon_code` in the request:**

```python
coupon_code = (ser.validated_data.get('coupon_code') or '').strip()
```

**How it currently calculates and returns `coupon_discount`:**

```python
discount = calculate_coupon_discount(coupon, cart_items, subtotal, shipping_amount, 'AED')
...
'coupon_discount': discount,
```

**Does it have any BXGY-specific logic?**

No. It delegates all coupon behavior to `coupon_is_applicable()` and `calculate_coupon_discount()`.

---

## 7. Coupon model

**File:** [offer/models.py](offer/models.py)

**Complete `Coupon` model definition:**

```python
class Coupon(models.Model):
    coupon_id = models.CharField(max_length=120)
    couponset_id = models.CharField(max_length=120, blank=True)
    org_id = models.IntegerField(db_index=True)
    coupon_name = models.CharField(max_length=255, blank=True)
    coupon_code = models.CharField(max_length=120, db_index=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=False)
    status = models.CharField(max_length=120, blank=True)
    rule_type = models.CharField(max_length=120, blank=True)
    coupon_type = models.CharField(max_length=120, blank=True)
    show_in_storefront = models.BooleanField(default=False)
    restrict_for_guest_user = models.BooleanField(default=False)
    restrict_for_offline_payments = models.BooleanField(default=False)
    stop_after_this_rule = models.BooleanField(default=False)
    apply_once_per_order = models.BooleanField(default=False)
    type = models.CharField(max_length=120, blank=True)
    duration = models.CharField(max_length=120, blank=True)
    discount_type = models.CharField(max_length=120, blank=True)
    discount_by = models.CharField(max_length=120, blank=True)
    apply_on = models.CharField(max_length=120, blank=True)
    discount_value = models.CharField(max_length=120, blank=True)
    discount_amounts = models.JSONField(default=list, blank=True)
    max_discount_amount = models.CharField(max_length=120, blank=True)
    max_redemption = models.IntegerField(default=0)
    max_redemption_count = models.IntegerField(default=0)
    redemption_count = models.IntegerField(default=0)
    max_redemption_count_per_user = models.IntegerField(default=0)
    max_usage_per_transaction = models.IntegerField(default=0)
    max_discounted_product_count_per_cart = models.CharField(max_length=120, blank=True)
    minimum_order_value = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    minimum_order_quantity = models.CharField(max_length=120, blank=True)
    activation_time = models.DateTimeField(null=True, blank=True)
    expiry_at = models.CharField(max_length=120, blank=True)
    expiry_time = models.DateTimeField(null=True, blank=True)
    eligible_products = models.JSONField(default=dict, blank=True)
    buy_products = models.JSONField(default=dict, blank=True)
    get_products = models.JSONField(default=dict, blank=True)
    eligible_customers = models.JSONField(default=dict, blank=True)
    eligible_shipping_zones = models.JSONField(default=dict, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

**Confirmed fields and types:**

- `buy_products` → `models.JSONField(default=dict, blank=True)`
- `get_products` → `models.JSONField(default=dict, blank=True)`
- `discount_value` → `models.CharField(max_length=120, blank=True)`
- `discount_type` → `models.CharField(max_length=120, blank=True)`
- `max_discounted_product_count_per_cart` → `models.CharField(max_length=120, blank=True)`
- `max_usage_per_transaction` → `models.IntegerField(default=0)`

---

## Notes on product lookup and BXGY behavior

- The checkout flow uses local `Product` rows fetched by `zoho_product_id`.
- BXGY checkout validation in `CheckoutAPIView.post` validates only the buy side now and then applies the client-provided `coupon_discount`.
- The order summary endpoint computes total discount only; it does not split BXGY discount by line item.
