# State-only: keep superuser_adminloginotp table for admin_dashboard.AdminLoginOTP.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('superuser', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(
                    name='AdminLoginOTP',
                ),
            ],
            database_operations=[],
        ),
    ]
