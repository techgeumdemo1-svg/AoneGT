from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, PasswordResetOTP, RegistrationOTP


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ('id',)
    list_display = ('id', 'email', 'phone', 'first_name', 'last_name', 'is_staff', 'is_active')
    search_fields = ('email', 'phone', 'first_name', 'last_name')
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'phone')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login',)}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'email', 'first_name', 'last_name', 'phone',
                'password1', 'password2', 'is_staff', 'is_active',
            )
        }),
    )


@admin.register(PasswordResetOTP)
class PasswordResetOTPAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'otp_code', 'is_used', 'created_at', 'expires_at')
    search_fields = ('user__email', 'otp_code')


@admin.register(RegistrationOTP)
class RegistrationOTPAdmin(admin.ModelAdmin):
    list_display = ('id', 'email', 'otp_code', 'is_used', 'created_at', 'expires_at')
    search_fields = ('email', 'otp_code')
