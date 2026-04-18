# Order sync states: pending_zoho_sync / synced / sync_failed + metadata fields.

from django.db import migrations, models
from django.utils import timezone as dj_tz


def forwards_status_and_timestamps(apps, schema_editor):
    Order = apps.get_model('shop', 'Order')
    Order.objects.filter(status='pending_zoho').update(status='pending_zoho_sync')
    now = dj_tz.now()
    Order.objects.filter(status='confirmed').update(status='synced', zoho_synced_at=now)


def noop_reverse(apps, schema_editor):
    Order = apps.get_model('shop', 'Order')
    Order.objects.filter(status='pending_zoho_sync').update(status='pending_zoho')
    Order.objects.filter(status='synced').update(status='confirmed', zoho_synced_at=None)
    Order.objects.filter(status='sync_failed').update(status='pending_zoho')


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0003_cart_one_per_user_line_store'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='zoho_sync_error',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='order',
            name='zoho_synced_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='order',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending_zoho_sync', 'Pending Zoho sync'),
                    ('synced', 'Synced'),
                    ('sync_failed', 'Zoho sync failed'),
                    ('cancelled', 'Cancelled'),
                ],
                default='pending_zoho_sync',
                max_length=32,
            ),
        ),
        migrations.RunPython(forwards_status_and_timestamps, noop_reverse),
    ]
