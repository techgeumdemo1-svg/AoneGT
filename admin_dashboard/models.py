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
