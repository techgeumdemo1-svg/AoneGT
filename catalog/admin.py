from django.contrib import admin
from .models import Banner, Store, Product, ProductReview


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
    list_display = ('name', 'store', 'category', 'sku', 'price', 'currency', 'is_active')
    list_filter = ('is_active', 'store', 'currency')
    search_fields = ('name', 'slug', 'category', 'sku', 'zoho_product_id')
    prepopulated_fields = {'slug': ('name',)}
    autocomplete_fields = ('store',)
    inlines = [ProductReviewInline]
