from django.contrib import admin
from .models import Cart, CartItem, Order, OrderItem


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'store', 'updated_at')
    list_filter = ('store',)
    inlines = [CartItemInline]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('line_total',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'store', 'status', 'total', 'currency', 'created_at')
    list_filter = ('status', 'store')
    search_fields = ('user__email', 'shipping_name', 'zoho_salesorder_id')
    inlines = [OrderItemInline]
    readonly_fields = ('created_at', 'updated_at')
