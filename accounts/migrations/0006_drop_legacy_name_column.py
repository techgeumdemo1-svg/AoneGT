from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0005_user_points_balance'),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE accounts_user DROP COLUMN IF EXISTS name;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
