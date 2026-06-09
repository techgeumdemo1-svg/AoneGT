from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0026_geidea_paybylink_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='geidea_merchant_ref',
            field=models.UUIDField(
                null=True,
                blank=True,
                unique=True,
                help_text='UUID sent to Geidea as merchantReferenceId. Generated on first payment initiation.',
            ),
        ),
    ]
