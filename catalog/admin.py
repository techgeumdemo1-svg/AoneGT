from django.contrib import admin
from .models import Store, Product


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'is_active', 'sort_order', 'zoho_site_id')
    list_filter = ('is_active',)
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'store', 'sku', 'price', 'currency', 'is_active')
    list_filter = ('is_active', 'store', 'currency')
    search_fields = ('name', 'slug', 'sku', 'zoho_product_id')
    prepopulated_fields = {'slug': ('name',)}
    autocomplete_fields = ('store',)
