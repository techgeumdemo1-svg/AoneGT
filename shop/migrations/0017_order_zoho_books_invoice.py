from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0016_order_customer_tracking_stage'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='zoho_books_invoice_id',
            field=models.CharField(
                blank=True,
                help_text='Zoho Books invoice_id after successful invoice creation.',
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='zoho_books_invoice_number',
            field=models.CharField(
                blank=True,
                help_text='Human-readable invoice number from Zoho Books.',
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='zoho_books_invoice_error',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='order',
            name='zoho_books_invoiced_at',
            field=models.DateTimeField(
                blank=True,
                help_text='When the Zoho Books invoice was created for this order.',
                null=True,
            ),
        ),
    ]
