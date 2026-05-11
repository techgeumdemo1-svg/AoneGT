from django.db import migrations, models


def normalize_address_types(apps, schema_editor):
    UserAddress = apps.get_model('shop', 'UserAddress')
    UserAddress.objects.filter(address_type='work').update(address_type='office')
    UserAddress.objects.filter(address_type='offive').update(address_type='office')
    UserAddress.objects.filter(address_type='other').update(address_type='apartments')


class Migration(migrations.Migration):
    dependencies = [
        ('shop', '0009_purchasepointsledger'),
    ]

    operations = [
        migrations.RunPython(normalize_address_types, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='useraddress',
            name='address_type',
            field=models.CharField(
                choices=[
                    ('home', 'Home'),
                    ('flat', 'Flat'),
                    ('office', 'Office'),
                    ('apartments', 'Apartments'),
                ],
                default='home',
                max_length=20,
            ),
        ),
    ]
