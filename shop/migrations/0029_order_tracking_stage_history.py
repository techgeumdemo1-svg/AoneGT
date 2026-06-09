from django.db import migrations, models


def add_column_if_missing(apps, schema_editor):
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'shop_order' AND column_name = 'tracking_stage_history'
            """
        )
        if cursor.fetchone():
            return
        cursor.execute(
            "ALTER TABLE shop_order "
            "ADD COLUMN tracking_stage_history jsonb NOT NULL DEFAULT '{}'"
        )


def backfill_tracking_history(apps, schema_editor):
    Order = apps.get_model('shop', 'Order')
    for order in Order.objects.iterator():
        if order.tracking_stage_history:
            continue
        history = {}
        if order.created_at:
            history['pending'] = order.created_at.isoformat()
        stage = (order.customer_tracking_stage or '').strip().lower()
        if stage == 'confirmed':
            stage = 'packed'
        if stage and order.updated_at:
            history[stage] = order.updated_at.isoformat()
        if order.status == 'cancelled' and order.updated_at:
            history['cancelled'] = order.updated_at.isoformat()
        if history:
            order.tracking_stage_history = history
            order.save(update_fields=['tracking_stage_history'])


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0028_order_tracking_stages'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='order',
                    name='tracking_stage_history',
                    field=models.JSONField(
                        blank=True,
                        default=dict,
                        help_text='Map of tracking stage key → ISO datetime when that stage was reached.',
                    ),
                ),
            ],
            database_operations=[
                migrations.RunPython(add_column_if_missing, migrations.RunPython.noop),
            ],
        ),
        migrations.RunPython(backfill_tracking_history, migrations.RunPython.noop),
    ]
