from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0012_loyalty_order_fields_and_issued_coupon'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderreturn',
            name='return_reason',
            field=models.CharField(
                blank=True,
                choices=[
                    ('damaged_product', 'Damaged product'),
                    ('wrong_item', 'Wrong item received'),
                    ('poor_quality', 'Poor quality'),
                    ('not_as_described', 'Not as described'),
                    ('changed_mind', 'Changed my mind'),
                    ('other', 'Other'),
                ],
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name='orderreturn',
            name='return_reason_detail',
            field=models.TextField(
                blank=True,
                help_text='Required when return_reason is "other"; optional extra context otherwise.',
            ),
        ),
    ]
