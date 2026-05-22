from django.db import migrations, models


def migrate_credit_debit_to_gateway(apps, schema_editor):
    Order = apps.get_model('shop', 'Order')
    Order.objects.filter(payment_method='credit_debit_card').update(
        payment_method='payment_gateway',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0018_order_zoho_books_payment'),
    ]

    operations = [
        migrations.RunPython(
            migrate_credit_debit_to_gateway,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='order',
            name='payment_method',
            field=models.CharField(
                choices=[
                    ('payment_gateway', 'Payment gateway'),
                    ('card_on_delivery', 'Card on Delivery'),
                    ('cash_on_delivery', 'Cash on Delivery'),
                    ('pay_by_link', 'Pay by Link'),
                ],
                default='cash_on_delivery',
                max_length=32,
            ),
        ),
    ]
