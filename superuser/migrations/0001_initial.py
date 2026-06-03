# Table may already exist on prod; admin_dashboard owns it via db_table=superuser_adminloginotp.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def _create_adminloginotp_table_if_missing(apps, schema_editor):
    table = 'superuser_adminloginotp'
    if table in schema_editor.connection.introspection.table_names():
        return
    model = apps.get_model('superuser', 'AdminLoginOTP')
    schema_editor.create_model(model)


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='AdminLoginOTP',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('otp_code', models.CharField(max_length=6)),
                        ('is_used', models.BooleanField(default=False)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('expires_at', models.DateTimeField()),
                        ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='admin_login_otps', to=settings.AUTH_USER_MODEL)),
                    ],
                    options={
                        'ordering': ['-created_at'],
                    },
                ),
            ],
            database_operations=[
                migrations.RunPython(
                    _create_adminloginotp_table_if_missing,
                    migrations.RunPython.noop,
                ),
            ],
        ),
    ]
