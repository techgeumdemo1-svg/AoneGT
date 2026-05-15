from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum

from catalog.models import Product, Store


class UserAddress(models.Model):
    class AddressType(models.TextChoices):
        HOME = 'home', 'Home'
        FLAT = 'flat', 'Flat'
        OFFICE = 'office', 'Office'
        APARTMENTS = 'apartments', 'Apartments'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='saved_addresses',
    )
    full_name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=50)
    address = models.CharField(max_length=500)
    city = models.CharField(max_length=120)
    state = models.CharField(max_length=120, blank=True)
    address_type = models.CharField(
        max_length=20,
        choices=AddressType.choices,
        default=AddressType.HOME,
    )
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_default', '-updated_at', '-created_at']

    def __str__(self):
        return f'{self.user_id}: {self.full_name} ({self.get_address_type_display()})'


class Cart(models.Model):
    """One basket per user; each line carries its ``store`` (multi-store cart)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='carts')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user'], name='shop_cart_user_uniq'),
        ]

    def __str__(self):
        return f'Cart {self.user.email}'


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='+')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='cart_items')
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['cart', 'product'], name='shop_cartitem_cart_product_uniq'),
        ]

    def __str__(self):
        return f'{self.quantity}× {self.product.name}'

    @property
    def line_subtotal(self) -> Decimal:
        return Decimal(self.product.price) * self.quantity


class WishlistItem(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wishlist_items')
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='+')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='wishlist_items')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'store', 'product'],
                name='shop_wishlist_user_store_product_uniq',
            ),
        ]

    def __str__(self):
        return f'{self.user_id} - {self.product.name}'


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING_ZOHO_SYNC = 'pending_zoho_sync', 'Pending Zoho sync'
        SYNCED = 'synced', 'Synced'
        SYNC_FAILED = 'sync_failed', 'Zoho sync failed'
        CANCELLED = 'cancelled', 'Cancelled'

    class PaymentMethod(models.TextChoices):
        GEIDEA = 'geidea', 'Geidea'
        CREDIT_DEBIT_CARD = 'credit_debit_card', 'Credit / Debit Card'
        CARD_ON_DELIVERY = 'card_on_delivery', 'Card on Delivery'
        CASH_ON_DELIVERY = 'cash_on_delivery', 'Cash on Delivery'
        PAY_BY_LINK = 'pay_by_link', 'Pay by Link'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='orders')
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='orders')
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING_ZOHO_SYNC,
    )
    currency = models.CharField(max_length=8, default='AED')
    payment_method = models.CharField(
        max_length=32,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH_ON_DELIVERY,
    )
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    vat_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('5.00'))
    vat_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    shipping_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))

    shipping_name = models.CharField(max_length=255)
    shipping_phone = models.CharField(max_length=50)
    shipping_address = models.CharField(max_length=500)
    shipping_city = models.CharField(max_length=120)
    shipping_state = models.CharField(max_length=120, blank=True)
    shipping_postal_code = models.CharField(max_length=32, blank=True)
    shipping_country = models.CharField(max_length=120)

    billing_same_as_shipping = models.BooleanField(default=True)
    billing_name = models.CharField(max_length=255, blank=True)
    billing_phone = models.CharField(max_length=50, blank=True)
    billing_address = models.CharField(max_length=500, blank=True)
    billing_city = models.CharField(max_length=120, blank=True)
    billing_state = models.CharField(max_length=120, blank=True)
    billing_postal_code = models.CharField(max_length=32, blank=True)
    billing_country = models.CharField(max_length=120, blank=True)

    zoho_checkout_id = models.CharField(max_length=255, blank=True)
    zoho_salesorder_id = models.CharField(max_length=120, blank=True)
    zoho_sync_error = models.TextField(blank=True)
    zoho_synced_at = models.DateTimeField(null=True, blank=True)

    loyalty_points_redeemed = models.PositiveIntegerField(
        default=0,
        help_text='Points spent at checkout or via issued coupon for this order.',
    )
    loyalty_discount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0'),
        help_text='Amount subtracted from total using loyalty (1 point = 1 AED by default).',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Order {self.pk} ({self.status})'


class LoyaltyIssuedCoupon(models.Model):
    """Store-credit style coupon: user exchanges wallet points for a one-time code."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='loyalty_issued_coupons',
    )
    code = models.CharField(max_length=32, unique=True, db_index=True)
    points_spent = models.PositiveIntegerField()
    amount_aed = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    order = models.OneToOneField(
        Order,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='loyalty_coupon_use',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.code} ({self.amount_aed} AED)'


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='order_items')
    product_name = models.CharField(max_length=255)
    sku = models.CharField(max_length=120, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField()
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    zoho_line_item_id = models.CharField(
        max_length=120,
        blank=True,
        help_text='Zoho sales order line id when synced (for sales returns API).',
    )

    class Meta:
        ordering = ['id']

    def __str__(self):
        return self.product_name

    def quantity_in_active_returns(self) -> int:
        agg = self.return_lines.filter(
            order_return__status__in=(
                'pending_zoho',
                'synced',
                'completed',
            ),
        ).aggregate(s=Sum('quantity'))
        return int(agg['s'] or 0)


class OrderReturn(models.Model):
    class Status(models.TextChoices):
        PENDING_ZOHO = 'pending_zoho', 'Pending Zoho sync'
        SYNCED = 'synced', 'Synced to Zoho'
        COMPLETED = 'completed', 'Completed'
        REJECTED = 'rejected', 'Rejected'
        FAILED = 'failed', 'Zoho sync failed'

    class ReturnReason(models.TextChoices):
        DAMAGED_PRODUCT = 'damaged_product', 'Damaged product'
        WRONG_ITEM = 'wrong_item', 'Wrong item received'
        POOR_QUALITY = 'poor_quality', 'Poor quality'
        NOT_AS_DESCRIBED = 'not_as_described', 'Not as described'
        CHANGED_MIND = 'changed_mind', 'Changed my mind'
        OTHER = 'other', 'Other'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='returns')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='order_returns',
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING_ZOHO,
    )
    zoho_salesreturn_id = models.CharField(max_length=120, blank=True)
    return_reason = models.CharField(
        max_length=32,
        choices=ReturnReason.choices,
        blank=True,
    )
    return_reason_detail = models.TextField(
        blank=True,
        help_text='Required when return_reason is "other"; optional extra context otherwise.',
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Return {self.pk} (order {self.order_id})'


class OrderReturnLine(models.Model):
    order_return = models.ForeignKey(OrderReturn, on_delete=models.CASCADE, related_name='lines')
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name='return_lines')
    quantity = models.PositiveIntegerField()

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.quantity}× item {self.order_item_id}'


class PurchasePointsLedger(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='purchase_points_ledger',
    )
    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name='points_ledger_entry',
    )
    points_awarded = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Points {self.points_awarded} for order {self.order_id}'


class UserNotification(models.Model):
    """In-app notification feed (offers, orders, wallet points, welcome offers)."""

    class Kind(models.TextChoices):
        OFFER = 'offer', 'Offer'
        ORDER = 'order', 'Order'
        POINTS_REWARD = 'points_reward', 'Points reward'
        POINTS_DEDUCTED = 'points_deducted', 'Points deducted'
        MEMBER_OFFER = 'member_offer', 'New member offer'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='shop_notifications',
    )
    kind = models.CharField(max_length=32, choices=Kind.choices, db_index=True)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['user', 'read_at']),
        ]

    def __str__(self):
        return f'{self.kind} → user {self.user_id}: {self.title[:40]}'


class FCMDeviceToken(models.Model):
    class DeviceType(models.TextChoices):
        ANDROID = 'android', 'android'
        IOS = 'ios', 'ios'
        WEB = 'web', 'web'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='fcm_tokens',
    )
    token = models.TextField(unique=True)
    device_type = models.CharField(max_length=10, choices=DeviceType.choices)
    is_active = models.BooleanField(default=True)
    push_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-created_at']

    def __str__(self):
        return f'{self.user_id}:{self.device_type}:{self.token[:24]}'
