from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('shop', '0010_normalize_useraddress_types'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='wishlistitem',
            name='shop_wishlist_user_product_uniq',
        ),
        migrations.AddConstraint(
            model_name='wishlistitem',
            constraint=models.UniqueConstraint(
                fields=('user', 'store', 'product'),
                name='shop_wishlist_user_store_product_uniq',
            ),
        ),
    ]
