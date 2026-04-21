from django.contrib import admin
from .models import Organization, WebhookConfig


class WebhookConfigInline(admin.TabularInline):
    model = WebhookConfig
    extra = 1
    fields = ['webhook_type', 'webhook_url', 'is_active']


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ['name', 'org_id', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'org_id']
    inlines = [WebhookConfigInline]


@admin.register(WebhookConfig)
class WebhookConfigAdmin(admin.ModelAdmin):
    list_display = ['organization', 'webhook_type', 'is_active']
    list_filter = ['webhook_type', 'is_active']