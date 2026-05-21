from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_change_password_otp'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='zoho_books_contact_id',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]
