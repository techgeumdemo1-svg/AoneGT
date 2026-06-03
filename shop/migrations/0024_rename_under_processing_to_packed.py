from django.db import migrations, models


def forwards_rename_stage(apps, schema_editor):
    Order = apps.get_model('shop', 'Order')
    Order.objects.filter(customer_tracking_stage='under_processing').update(
        customer_tracking_stage='packed',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0023_add_delivery_zone'),
    ]

    operations = [
        migrations.RunPython(forwards_rename_stage, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='order',
            name='customer_tracking_stage',
            field=models.CharField(
                blank=True,
                choices=[
                    ('pending', 'Pending'),
                    ('confirmed', 'Confirmed'),
                    ('packed', 'Packed'),
                    ('out_for_delivery', 'Out for Delivery'),
                    ('delivered', 'Delivered'),
                ],
                help_text='Customer-facing delivery stage for the app tracking rail and emails.',
                max_length=32,
            ),
        ),
    ]
