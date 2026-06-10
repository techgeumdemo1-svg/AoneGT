from django.contrib import admin
from .models import Banner, Store, Product, ProductReview, ZohoBooksStoreConfig


class ProductReviewInline(admin.TabularInline):
    model = ProductReview
    extra = 0
    readonly_fields = ('user', 'rating', 'title', 'created_at')
    can_delete = True


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
        'zoho_books_org_id',
        'zoho_store_domain',
        'created_at',
    )
    list_filter = ('is_active',)
    search_fields = ('name', 'slug', 'contact_email', 'category', 'zoho_org_id', 'zoho_books_org_id')
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
            'Zoho Books (invoices)',
            {'fields': ('zoho_books_org_id', 'zoho_books_vat_tax_id')},
        ),
        (
            'Zoho OAuth (optional; per-store — falls back to global env)',
            {'fields': ('client_id', 'client_secret', 'refresh_token', 'access_token', 'token_expiry')},
        ),
        ('Meta', {'fields': ('created_at',)}),
    )


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'store', 'sort_order', 'is_active', 'updated_at')
    list_filter = ('is_active', 'store')
    search_fields = ('title', 'subtitle', 'image_url')
    autocomplete_fields = ('store',)


@admin.register(ProductReview)
class ProductReviewAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'user', 'rating', 'title', 'created_at')
    list_filter = ('rating', 'created_at')
    search_fields = ('title', 'body', 'product__name', 'user__email')
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ('product', 'user')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'store',
        'category',
        'sku',
        'price',
        'currency',
        'is_best_deal',
        'best_deal_sort_order',
        'is_active',
    )
    list_filter = ('is_active', 'is_best_deal', 'store', 'currency')
    list_editable = ('is_best_deal', 'best_deal_sort_order')
    search_fields = ('name', 'slug', 'category', 'sku', 'zoho_product_id')
    prepopulated_fields = {'slug': ('name',)}
    autocomplete_fields = ('store',)
    inlines = [ProductReviewInline]
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'store',
                    'name',
                    'slug',
                    'category',
                    'sku',
                    'description',
                    'price',
                    'compare_at_price',
                    'currency',
                    'image_url',
                    'is_active',
                ),
            },
        ),
        (
            'Best deals (app)',
            {
                'fields': ('is_best_deal', 'best_deal_sort_order'),
                'description': (
                    'Mark products to feature in GET /zoho/multi/best-deals/ (source=admin). '
                    'Requires zoho_product_id for live price/stock from Zoho.'
                ),
            },
        ),
        (
            'Zoho Commerce',
            {'fields': ('zoho_product_id', 'zoho_category_id', 'zoho_collection_id')},
        ),
    )


@admin.register(ZohoBooksStoreConfig)
class ZohoBooksStoreConfigAdmin(admin.ModelAdmin):
    list_display = (
        'store',
        'deposit_account_id',
        'charge_account_id',
        'gateway_charge_rate',
        'paylink_charge_rate',
        'cod_charge_rate',
        'journal_gateway_enabled',
        'journal_paylink_enabled',
        'journal_cod_enabled',
        'updated_at',
    )
    search_fields = ('store__name',)
    fieldsets = (
        ('Store', {'fields': ('store',)}),
        (
            'Deposit Account',
            {'fields': ('deposit_account_id', 'deposit_account_name')},
        ),
        (
            'Payment Charges Account (shared)',
            {'fields': ('charge_account_id', 'charge_account_name')},
        ),
        (
            'VAT on Charges Account (shared)',
            {'fields': ('vat_account_id', 'vat_account_name')},
        ),
        (
            'Charge Rates (%)',
            {'fields': ('gateway_charge_rate', 'paylink_charge_rate', 'cod_charge_rate')},
        ),
        (
            'VAT Rates on Charge (%)',
            {'fields': ('gateway_vat_rate', 'paylink_vat_rate', 'cod_vat_rate')},
        ),
        (
            'Journal Automation',
            {'fields': ('journal_gateway_enabled', 'journal_paylink_enabled', 'journal_cod_enabled')},
        ),
    )
