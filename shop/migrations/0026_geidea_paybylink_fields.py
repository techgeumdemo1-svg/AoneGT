from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0025_add_geidea_merchant_ref_to_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='geidea_paylink_intent_id',
            field=models.CharField(
                max_length=255,
                blank=True,
                default='',
                help_text='Geidea paymentIntentId returned by eInvoice API. Used to cancel the link.',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='geidea_paylink_url',
            field=models.CharField(
                max_length=500,
                blank=True,
                default='',
                help_text='Full Geidea hosted payment URL returned by eInvoice API.',
            ),
        ),
        migrations.AddField(
            model_name='orderreturn',
            name='geidea_refund_id',
            field=models.CharField(
                max_length=255,
                blank=True,
                default='',
                help_text='Geidea refund transaction ID. Non-empty = refund already processed (idempotency guard).',
            ),
        ),
    ]
