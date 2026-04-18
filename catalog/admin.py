from django.contrib import admin
from .models import Store, Product


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'contact_email',
        'category',
        'slug',
        'is_active',
        'sort_order',
        'zoho_org_id',
        'zoho_store_domain',
        'created_at',
    )
    list_filter = ('is_active',)
    search_fields = ('name', 'slug', 'contact_email', 'category', 'zoho_org_id')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('created_at',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'name',
                    'slug',
                    'contact_email',
                    'category',
                    'description',
                    'logo_url',
                    'is_active',
                    'sort_order',
                )
            },
        ),
        ('Zoho Commerce', {'fields': ('zoho_org_id', 'zoho_store_domain')}),
        (
            'Zoho OAuth (optional; per-store — falls back to global env)',
            {'fields': ('client_id', 'client_secret', 'refresh_token', 'access_token', 'token_expiry')},
        ),
        ('Meta', {'fields': ('created_at',)}),
    )


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'store', 'category', 'sku', 'price', 'currency', 'is_active')
    list_filter = ('is_active', 'store', 'currency')
    search_fields = ('name', 'slug', 'category', 'sku', 'zoho_product_id')
    prepopulated_fields = {'slug': ('name',)}
    autocomplete_fields = ('store',)
