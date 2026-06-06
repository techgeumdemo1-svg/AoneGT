from django.contrib import admin

from .models import AdminLoginOTP, CMSPage, FAQ


@admin.register(AdminLoginOTP)
class AdminLoginOTPAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'otp_code', 'is_used', 'created_at', 'expires_at')
    search_fields = ('user__email', 'otp_code')
    readonly_fields = ('otp_code', 'created_at', 'expires_at')


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    list_display = ('id', 'question', 'sort_order', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('question', 'answer')
    ordering = ('sort_order', 'id')


@admin.register(CMSPage)
class CMSPageAdmin(admin.ModelAdmin):
    list_display = ('id', 'slug', 'title', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('slug', 'title', 'content')
    readonly_fields = ('slug', 'created_at', 'updated_at')
