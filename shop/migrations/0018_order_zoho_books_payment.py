from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0017_order_zoho_books_invoice'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='zoho_books_paid_at',
            field=models.DateTimeField(
                blank=True,
                help_text='When payment was recorded in Zoho Books for this order.',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='zoho_books_payment_error',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='order',
            name='zoho_books_payment_id',
            field=models.CharField(
                blank=True,
                help_text='Zoho Books customerpayment id after payment is recorded.',
                max_length=64,
            ),
        ),
    ]
