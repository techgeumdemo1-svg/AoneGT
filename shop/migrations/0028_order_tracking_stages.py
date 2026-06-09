from django.db import migrations, models


def forwards_migrate_stages(apps, schema_editor):
    Order = apps.get_model('shop', 'Order')
    Order.objects.filter(customer_tracking_stage='confirmed').update(
        customer_tracking_stage='packed',
    )
    Order.objects.filter(customer_tracking_stage='under_processing').update(
        customer_tracking_stage='packed',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0027_order_geidea_merchant_ref_unique'),
    ]

    operations = [
        migrations.RunPython(forwards_migrate_stages, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='order',
            name='customer_tracking_stage',
            field=models.CharField(
                blank=True,
                choices=[
                    ('pending', 'Pending'),
                    ('packed', 'Packed'),
                    ('out_for_delivery', 'Out for Delivery'),
                    ('delivered', 'Delivered'),
                    ('returned', 'Returned'),
                ],
                help_text='Customer-facing delivery stage for the app tracking rail and emails.',
                max_length=32,
            ),
        ),
    ]
