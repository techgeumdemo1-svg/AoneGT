from django.contrib import admin

from .models import ZohoCommerceAccount


@admin.register(ZohoCommerceAccount)
class ZohoCommerceAccountAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'email', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'email')
    readonly_fields = ('created_at',)
