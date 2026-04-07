from django.db import models


class Store(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
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
    created_at = models.DateTimeField(auto_now_add=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


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
