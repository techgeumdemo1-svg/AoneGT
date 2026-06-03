# Creates superuser_adminloginotp only if missing (table may exist from superuser.0001 on prod).

from django.db import migrations


def _create_adminloginotp_table_if_missing(apps, schema_editor):
    table = 'superuser_adminloginotp'
    if table in schema_editor.connection.introspection.table_names():
        return
    model = apps.get_model('admin_dashboard', 'AdminLoginOTP')
    schema_editor.create_model(model)


class Migration(migrations.Migration):

    dependencies = [
        ('admin_dashboard', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[],
            database_operations=[
                migrations.RunPython(
                    _create_adminloginotp_table_if_missing,
                    migrations.RunPython.noop,
                ),
            ],
        ),
    ]
