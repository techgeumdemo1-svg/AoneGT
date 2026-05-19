from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0008_product_zoho_category_collection_ids'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='best_deal_sort_order',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Lower numbers appear first in Best Deals.',
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='is_best_deal',
            field=models.BooleanField(
                default=False,
                help_text='Show this product in the app Best Deals section (curated in Django admin).',
            ),
        ),
    ]
