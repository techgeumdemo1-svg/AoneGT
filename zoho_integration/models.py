from django.db import models


class ZohoCommerceAccount(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    organization_id = models.CharField(max_length=100, null=True, blank=True)
    accounts_url = models.URLField(default='https://accounts.zoho.com')
    commerce_base_url = models.URLField(default='https://commerce.zoho.com')

    client_id = models.CharField(max_length=255)
    client_secret = models.CharField(max_length=255)
    refresh_token = models.TextField()

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.email})'
