from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0015_add_fcm_device_token'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='customer_tracking_stage',
            field=models.CharField(
                blank=True,
                choices=[
                    ('pending', 'Pending'),
                    ('confirmed', 'Confirmed'),
                    ('under_processing', 'Under Processing'),
                    ('out_for_delivery', 'Out for Delivery'),
                    ('delivered', 'Delivered'),
                ],
                help_text='Customer-facing delivery stage for the app tracking rail and emails.',
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='out_for_delivery_email_sent_at',
            field=models.DateTimeField(
                blank=True,
                help_text='Set when the out-for-delivery email was sent to the customer.',
                null=True,
            ),
        ),
    ]
