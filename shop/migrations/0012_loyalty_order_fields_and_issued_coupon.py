from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('shop', '0011_wishlist_per_store_unique'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='loyalty_discount',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0'),
                help_text='Amount subtracted from total using loyalty (1 point = 1 AED by default).',
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='loyalty_points_redeemed',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Points spent at checkout or via issued coupon for this order.',
            ),
        ),
        migrations.CreateModel(
            name='LoyaltyIssuedCoupon',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(db_index=True, max_length=32, unique=True)),
                ('points_spent', models.PositiveIntegerField()),
                ('amount_aed', models.DecimalField(decimal_places=2, max_digits=12)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                (
                    'order',
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='loyalty_coupon_use',
                        to='shop.order',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='loyalty_issued_coupons',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
