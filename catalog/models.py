from django.conf import settings
from django.db import models


class Store(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    contact_email = models.EmailField(blank=True)
    category = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    logo_url = models.URLField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    zoho_org_id = models.CharField(
        max_length=120,
        blank=True,
        help_text=(
            'Zoho Commerce organization id (header X-com-zoho-store-organizationid). '
            'Per-store; falls back to ZOHO_COMMERCE_ORGANIZATION_ID when empty.'
        ),
    )
    zoho_store_domain = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            'Storefront host for Zoho (e.g. mystore.zohostore.com), sent as domain-name. '
            'Per-store; falls back to ZOHO_STORE_DOMAIN when empty.'
        ),
    )
    client_id = models.CharField(max_length=255, blank=True)
    client_secret = models.CharField(max_length=255, blank=True)
    refresh_token = models.TextField(blank=True)
    access_token = models.TextField(blank=True)
    token_expiry = models.DateTimeField(null=True, blank=True)
    zoho_books_org_id = models.CharField(
        max_length=120,
        blank=True,
        help_text=(
            'Zoho Books organization id for invoices (per store). '
            'Falls back to ZOHO_BOOKS_ORGANIZATION_ID in .env when empty.'
        ),
    )
    zoho_books_vat_tax_id = models.CharField(
        max_length=120,
        blank=True,
        help_text='Optional Zoho Books tax_id for VAT on invoice line items for this store.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class Banner(models.Model):
    """Promotional carousel image for app storefront (optional per-store scope)."""

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name='banners',
        null=True,
        blank=True,
        help_text='If empty, banner applies to all stores when listing without filter.',
    )
    title = models.CharField(max_length=200, blank=True)
    subtitle = models.CharField(max_length=300, blank=True)
    image_url = models.URLField(max_length=500)
    link_url = models.URLField(max_length=500, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.title or f'Banner {self.pk}'


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
    is_best_deal = models.BooleanField(
        default=False,
        help_text='Show this product in the app Best Deals section (curated in Django admin).',
    )
    best_deal_sort_order = models.PositiveIntegerField(
        default=0,
        help_text='Lower numbers appear first in Best Deals.',
    )
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

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['store', 'slug'],
                name='catalog_product_store_slug_uniq',
            ),
        ]

    def __str__(self):
        return f'{self.name} ({self.store.name})'


class ProductReview(models.Model):
    """One review per user per product; only after a delivered (synced) order containing the product."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='product_reviews',
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='reviews',
    )
    rating = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'product'],
                name='catalog_productreview_user_product_uniq',
            ),
        ]

    def __str__(self):
        return f'{self.rating}★ by user {self.user_id} on product {self.product_id}'


class ZohoBooksStoreConfig(models.Model):
    """
    Per-store Zoho Books financial configuration.
    Holds deposit account, charge accounts, rates, and journal enable flags.
    All account IDs are Zoho Books chart-of-account IDs for the store's org.
    """
    from decimal import Decimal as _Decimal

    store = models.OneToOneField(
        Store,
        on_delete=models.CASCADE,
        related_name='zoho_books_config',
    )

    # Deposit account — where received customer payment money lands
    deposit_account_id = models.CharField(
        max_length=120,
        blank=True,
        help_text='Zoho Books account_id for the bank/deposit account. '
                  'Used as account_id in customerpayments POST body.',
    )
    deposit_account_name = models.CharField(
        max_length=255,
        blank=True,
        help_text='Display label only — for admin reference.',
    )

    # Shared payment charges account (used for all payment methods)
    charge_account_id = models.CharField(
        max_length=120,
        blank=True,
        help_text='Zoho Books account_id for payment processing charges (expense account).',
    )
    charge_account_name = models.CharField(
        max_length=255,
        blank=True,
        help_text='Display label only.',
    )

    # VAT on charges sub-account
    vat_account_id = models.CharField(
        max_length=120,
        blank=True,
        help_text='Zoho Books account_id for VAT on payment charges (sub-account of charge_account).',
    )
    vat_account_name = models.CharField(
        max_length=255,
        blank=True,
        help_text='Display label only.',
    )

    # Charge rates (percent — e.g. 2.50 means 2.50%)
    gateway_charge_rate = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=_Decimal('2.50'),
        help_text='Geidea HPP payment gateway charge rate (%).',
    )
    paylink_charge_rate = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=_Decimal('2.50'),
        help_text='Geidea Pay by Link charge rate (%).',
    )
    cod_charge_rate = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=_Decimal('1.60'),
        help_text='Geidea POS / card on delivery charge rate (%).',
    )

    # VAT rates on charge (percent of charge amount)
    gateway_vat_rate = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=_Decimal('5.00'),
        help_text='VAT rate applied to gateway charge amount (%).',
    )
    paylink_vat_rate = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=_Decimal('5.00'),
        help_text='VAT rate applied to pay-by-link charge amount (%).',
    )
    cod_vat_rate = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=_Decimal('5.00'),
        help_text='VAT rate applied to COD charge amount (%).',
    )

    # Journal automation enable flags
    journal_gateway_enabled = models.BooleanField(
        default=False,
        help_text='Automatically create journal entries for payment_gateway orders.',
    )
    journal_paylink_enabled = models.BooleanField(
        default=False,
        help_text='Automatically create journal entries for pay_by_link orders.',
    )
    journal_cod_enabled = models.BooleanField(
        default=False,
        help_text='Automatically create journal entries for card_on_delivery orders.',
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Zoho Books Store Config'
        verbose_name_plural = 'Zoho Books Store Configs'

    def __str__(self):
        return f'ZohoBooksStoreConfig for {self.store.name}'
