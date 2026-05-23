from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0020_rename_geidea_to_payment_gateway'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='zoho_books_salesorder_id',
            field=models.CharField(
                blank=True,
                help_text='Zoho Books salesorder_id after successful sales order creation.',
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='zoho_books_salesorder_number',
            field=models.CharField(
                blank=True,
                help_text='Human-readable sales order number from Zoho Books.',
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='zoho_books_salesorder_error',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='order',
            name='zoho_books_salesordered_at',
            field=models.DateTimeField(
                blank=True,
                help_text='When the Zoho Books sales order was created for this order.',
                null=True,
            ),
        ),
    ]
