from django.contrib import admin
from .models import Cart, CartItem, Order, OrderItem, OrderReturn, OrderReturnLine


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'updated_at')
    search_fields = ('user__email',)
    inlines = [CartItemInline]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('line_total',)


class OrderReturnLineInline(admin.TabularInline):
    model = OrderReturnLine
    extra = 0


@admin.register(OrderReturn)
class OrderReturnAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'user', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('order__id', 'user__email', 'zoho_salesreturn_id')
    inlines = [OrderReturnLineInline]
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'store', 'status', 'total', 'currency', 'zoho_synced_at', 'created_at')
    list_filter = ('status', 'store')
    search_fields = ('user__email', 'shipping_name', 'zoho_salesorder_id')
    inlines = [OrderItemInline]
    readonly_fields = ('created_at', 'updated_at', 'zoho_synced_at', 'zoho_sync_error')
