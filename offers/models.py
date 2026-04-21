from django.db import models


class Organization(models.Model):
    """
    Represents a Zoho Commerce store.
    org_id is the Zoho organization ID (e.g. "60070045641").
    """
    name = models.CharField(max_length=255)
    image = models.ImageField(upload_to='org_images/', null=True, blank=True)
    org_id = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.org_id})"


class WebhookConfig(models.Model):
    """
    Stores the full Zoho ZAPI key webhook URL per organization per action.

    The webhook_url is the complete URL including encapiKey query param,
    copied directly from Zoho Commerce → Settings → Developer Data →
    Incoming Webhooks → [webhook name] → ZAPI Key URL.

    Example:
    https://www.zohoapis.in/commerce/v1/settings/incomingwebhooks/
    iw_create_coupon_webhook/execute?auth_type=apikey&encapiKey=PHtE6r...

    Django POSTs JSON to this URL. No OAuth tokens are stored here.
    OAuth is handled entirely inside Zoho's Connection system.
    """
    WEBHOOK_TYPE_CHOICES = [
        ('create_coupon', 'Create Coupon'),
        ('list_coupons', 'List Coupons'),
        ('delete_coupon', 'Delete Coupon'),
    ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='webhooks'
    )
    webhook_type = models.CharField(max_length=50, choices=WEBHOOK_TYPE_CHOICES)
    webhook_url = models.TextField(
        help_text="Full ZAPI key URL from Zoho incoming webhook settings. Includes encapiKey."
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('organization', 'webhook_type')

    def __str__(self):
        return f"{self.organization.name} — {self.get_webhook_type_display()}"