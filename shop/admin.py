from django.contrib import admin

from .models import (
    Cart,
    CartItem,
    FCMDeviceToken,
    Order,
    OrderItem,
    OrderReturn,
    OrderReturnLine,
    UserNotification,
)
from .services.order_email import handle_customer_tracking_stage_change


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
    list_display = ('id', 'order', 'user', 'status', 'return_reason', 'created_at')
    list_filter = ('status',)
    search_fields = ('order__id', 'user__email', 'zoho_salesreturn_id')
    inlines = [OrderReturnLineInline]
    readonly_fields = ('created_at', 'updated_at')


@admin.register(UserNotification)
class UserNotificationAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'kind', 'title', 'read_at', 'created_at')
    list_filter = ('kind', 'read_at')
    search_fields = ('user__email', 'title')
    readonly_fields = ('created_at',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'user',
        'store',
        'status',
        'customer_tracking_stage',
        'total',
        'currency',
        'zoho_synced_at',
        'created_at',
    )
    list_filter = ('status', 'customer_tracking_stage', 'store')
    search_fields = ('user__email', 'shipping_name', 'zoho_salesorder_id')
    inlines = [OrderItemInline]
    readonly_fields = (
        'created_at',
        'updated_at',
        'zoho_synced_at',
        'zoho_sync_error',
        'out_for_delivery_email_sent_at',
    )
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'user',
                    'store',
                    'status',
                    'customer_tracking_stage',
                    'out_for_delivery_email_sent_at',
                    'payment_method',
                    'currency',
                    'subtotal',
                    'vat_percent',
                    'vat_amount',
                    'shipping_amount',
                    'total',
                ),
            },
        ),
        (
            'Shipping',
            {
                'fields': (
                    'shipping_name',
                    'shipping_phone',
                    'shipping_address',
                    'shipping_city',
                    'shipping_state',
                    'shipping_postal_code',
                    'shipping_country',
                ),
            },
        ),
        (
            'Billing',
            {
                'fields': (
                    'billing_same_as_shipping',
                    'billing_name',
                    'billing_phone',
                    'billing_address',
                    'billing_city',
                    'billing_state',
                    'billing_postal_code',
                    'billing_country',
                ),
            },
        ),
        (
            'Zoho',
            {
                'fields': (
                    'zoho_checkout_id',
                    'zoho_salesorder_id',
                    'zoho_sync_error',
                    'zoho_synced_at',
                    'zoho_books_invoice_id',
                    'zoho_books_invoice_number',
                    'zoho_books_invoiced_at',
                    'zoho_books_invoice_error',
                    'zoho_books_salesorder_id',
                    'zoho_books_salesorder_number',
                    'zoho_books_salesordered_at',
                    'zoho_books_salesorder_error',
                    'zoho_books_payment_id',
                    'zoho_books_paid_at',
                    'zoho_books_payment_error',
                ),
            },
        ),
        (
            'Loyalty',
            {'fields': ('loyalty_points_redeemed', 'loyalty_discount')},
        ),
        ('Meta', {'fields': ('created_at', 'updated_at')}),
    )

    def save_model(self, request, obj, form, change):
        previous_stage = None
        if change and obj.pk:
            previous_stage = (
                Order.objects.filter(pk=obj.pk)
                .values_list('customer_tracking_stage', flat=True)
                .first()
            )
        super().save_model(request, obj, form, change)
        if obj.status == Order.Status.SYNCED:
            handle_customer_tracking_stage_change(obj, previous_stage)


admin.site.register(FCMDeviceToken)
