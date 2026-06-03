from django.contrib import admin

from .models import AdminLoginOTP


@admin.register(AdminLoginOTP)
class AdminLoginOTPAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'otp_code', 'is_used', 'created_at', 'expires_at')
    search_fields = ('user__email', 'otp_code')
    readonly_fields = ('otp_code', 'created_at', 'expires_at')
