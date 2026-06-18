from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def seed_loyalty_program_settings(apps, schema_editor):
    LoyaltyProgramSettings = apps.get_model('shop', 'LoyaltyProgramSettings')
    LoyaltyProgramSettings.objects.get_or_create(
        pk=1,
        defaults={
            'aed_per_point_earned': max(1, int(getattr(settings, 'LOYALTY_AED_PER_POINT_EARNED', 100))),
            'point_value_aed': Decimal(str(getattr(settings, 'LOYALTY_POINT_VALUE_AED', '1'))),
            'min_points_to_redeem': max(0, int(getattr(settings, 'LOYALTY_MIN_POINTS_TO_REDEEM', 100))),
            'coupon_points_block': max(1, int(getattr(settings, 'LOYALTY_COUPON_POINTS_BLOCK', 100))),
            'coupon_credit_aed': Decimal(str(getattr(settings, 'LOYALTY_COUPON_CREDIT_AED', '100'))),
            'coupon_expiry_days': max(1, int(getattr(settings, 'LOYALTY_COUPON_EXPIRY_DAYS', 90))),
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('shop', '0031_support_tickets'),
    ]

    operations = [
        migrations.CreateModel(
            name='LoyaltyProgramSettings',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('aed_per_point_earned', models.PositiveIntegerField(default=100)),
                ('point_value_aed', models.DecimalField(decimal_places=2, default=Decimal('1'), max_digits=12)),
                ('min_points_to_redeem', models.PositiveIntegerField(default=100)),
                ('coupon_points_block', models.PositiveIntegerField(default=100)),
                ('coupon_credit_aed', models.DecimalField(decimal_places=2, default=Decimal('100'), max_digits=12)),
                ('coupon_expiry_days', models.PositiveIntegerField(default=90)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'updated_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='loyalty_program_settings_updates',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'verbose_name': 'Loyalty program settings',
                'verbose_name_plural': 'Loyalty program settings',
            },
        ),
        migrations.RunPython(seed_loyalty_program_settings, migrations.RunPython.noop),
    ]
