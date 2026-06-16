from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_user_credit_balance_aed'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='admin_mfa_enabled',
            field=models.BooleanField(
                default=False,
                help_text='When enabled, admin dashboard login requires email OTP after password.',
            ),
        ),
    ]
