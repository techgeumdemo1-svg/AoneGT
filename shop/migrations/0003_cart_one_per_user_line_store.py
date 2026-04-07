# Generated manually — PDF workflow: one cart per user; store on each line.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_cartitem_store(apps, schema_editor):
    CartItem = apps.get_model('shop', 'CartItem')
    for item in CartItem.objects.select_related('cart').iterator():
        if item.store_id is None and getattr(item, 'cart_id', None):
            item.store_id = item.cart.store_id
            item.save(update_fields=['store_id'])


def merge_carts_per_user(apps, schema_editor):
    Cart = apps.get_model('shop', 'Cart')
    CartItem = apps.get_model('shop', 'CartItem')
    user_ids = Cart.objects.values_list('user_id', flat=True).distinct()
    for uid in user_ids:
        carts = list(Cart.objects.filter(user_id=uid).order_by('pk'))
        if len(carts) <= 1:
            continue
        keeper = carts[0]
        for c in carts[1:]:
            CartItem.objects.filter(cart_id=c.pk).update(cart_id=keeper.pk)
            c.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0002_order_returns_and_zoho_line'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='cartitem',
            name='store',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='+',
                to='catalog.store',
            ),
        ),
        migrations.RunPython(backfill_cartitem_store, migrations.RunPython.noop),
        migrations.RunPython(merge_carts_per_user, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='cart',
            name='shop_cart_user_store_uniq',
        ),
        migrations.RemoveField(
            model_name='cart',
            name='store',
        ),
        migrations.AddConstraint(
            model_name='cart',
            constraint=models.UniqueConstraint(fields=('user',), name='shop_cart_user_uniq'),
        ),
        migrations.AlterField(
            model_name='cartitem',
            name='store',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='+',
                to='catalog.store',
            ),
        ),
    ]
