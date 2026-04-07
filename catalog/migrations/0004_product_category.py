from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0003_store_zoho_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='category',
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
