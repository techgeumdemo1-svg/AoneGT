from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0009_product_best_deal_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='store',
            name='zoho_books_org_id',
            field=models.CharField(
                blank=True,
                help_text=(
                    'Zoho Books organization id for invoices (Doorde / Spices / Grocery each '
                    'have their own). Falls back to ZOHO_BOOKS_ORGANIZATION_ID in .env when empty.'
                ),
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='zoho_books_vat_tax_id',
            field=models.CharField(
                blank=True,
                help_text='Optional Zoho Books tax_id for VAT on invoice line items for this store.',
                max_length=120,
            ),
        ),
    ]
