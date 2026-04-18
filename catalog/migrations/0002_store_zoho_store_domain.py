from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='store',
            name='zoho_site_id',
            field=models.CharField(
                blank=True,
                help_text=(
                    'Zoho Commerce organization id (header X-com-zoho-store-organizationid). '
                    'Per-store; falls back to ZOHO_COMMERCE_ORGANIZATION_ID when empty.'
                ),
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='zoho_store_domain',
            field=models.CharField(
                blank=True,
                help_text=(
                    'Storefront host for Zoho (e.g. mystore.zohostore.com), sent as domain-name. '
                    'Per-store; falls back to ZOHO_STORE_DOMAIN when empty.'
                ),
                max_length=255,
            ),
        ),
    ]
