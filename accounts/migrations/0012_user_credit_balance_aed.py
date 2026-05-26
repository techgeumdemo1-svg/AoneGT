from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0011_user_zoho_books_contact_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='credit_balance_aed',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0'),
                help_text='Prepaid account credit (AED) from gateway/paylink payments and refunds.',
                max_digits=12,
            ),
        ),
    ]
