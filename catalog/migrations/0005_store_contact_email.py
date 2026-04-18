from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0004_product_category'),
    ]

    operations = [
        migrations.AddField(
            model_name='store',
            name='contact_email',
            field=models.EmailField(blank=True, max_length=254),
        ),
    ]
