import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0002_store_zoho_store_domain'),
    ]

    operations = [
        migrations.RenameField(
            model_name='store',
            old_name='zoho_site_id',
            new_name='zoho_org_id',
        ),
        migrations.AddField(
            model_name='store',
            name='category',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='store',
            name='client_id',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='store',
            name='client_secret',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='store',
            name='refresh_token',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='store',
            name='access_token',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='store',
            name='token_expiry',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='store',
            name='created_at',
            field=models.DateTimeField(
                auto_now_add=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
    ]
