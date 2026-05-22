from django.db import migrations, models


def rename_geidea_to_payment_gateway(apps, schema_editor):
    Order = apps.get_model('shop', 'Order')
    Order.objects.filter(payment_method='geidea').update(payment_method='payment_gateway')


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0019_payment_method_choices'),
    ]

    operations = [
        migrations.RunPython(
            rename_geidea_to_payment_gateway,
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
