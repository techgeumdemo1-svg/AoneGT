# AdminLoginOTP table already exists as superuser_adminloginotp (from superuser app).

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


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
                        'db_table': 'superuser_adminloginotp',
                        'ordering': ['-created_at'],
                    },
                ),
            ],
            database_operations=[],
        ),
    ]
