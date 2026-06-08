import random
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class AdminLoginOTP(models.Model):
    """One-time code sent after successful admin email/password check."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='admin_login_otps',
    )
    otp_code = models.CharField(max_length=6)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = 'superuser_adminloginotp'
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.otp_code:
            self.otp_code = f'{random.randint(100000, 999999)}'
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(minutes=10)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at


class FAQ(models.Model):
    question = models.CharField(max_length=500)
    answer = models.TextField()
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'id']
        verbose_name = 'FAQ'
        verbose_name_plural = 'FAQs'

    def __str__(self):
        return self.question[:80]


class CMSPage(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['slug']
        verbose_name = 'CMS page'
        verbose_name_plural = 'CMS pages'

    def __str__(self):
        return self.title


class AdminActivityLog(models.Model):
    """Audit trail of staff actions in the admin dashboard."""

    class Category(models.TextChoices):
        ORDERS = "orders", "Orders"
        RETURNS = "returns", "Returns"
        CUSTOMERS = "customers", "Customers"
        USERS = "users", "Admin users"
        CMS = "cms", "CMS"
        BANNERS = "banners", "Banners"
        STORES = "stores", "Stores"
        DELIVERY_ZONES = "delivery_zones", "Delivery zones"
        AUTH = "auth", "Authentication"
        SYSTEM = "system", "System"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_activity_logs",
    )
    actor_email = models.EmailField(blank=True)
    category = models.CharField(max_length=32, choices=Category.choices)
    action = models.CharField(max_length=64)
    message = models.CharField(max_length=500)
    target_type = models.CharField(max_length=32, blank=True)
    target_id = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Admin activity log"
        verbose_name_plural = "Admin activity logs"

    def __str__(self):
        return f"{self.category}:{self.action} ({self.created_at})"
