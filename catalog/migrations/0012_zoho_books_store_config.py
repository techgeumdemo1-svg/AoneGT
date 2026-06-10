from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0011_alter_store_zoho_books_org_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='ZohoBooksStoreConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('store', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='zoho_books_config',
                    to='catalog.store',
                )),
                ('deposit_account_id', models.CharField(blank=True, max_length=120, help_text='Zoho Books account_id for the bank/deposit account.')),
                ('deposit_account_name', models.CharField(blank=True, max_length=255, help_text='Display label only.')),
                ('charge_account_id', models.CharField(blank=True, max_length=120, help_text='Zoho Books account_id for payment processing charges.')),
                ('charge_account_name', models.CharField(blank=True, max_length=255, help_text='Display label only.')),
                ('vat_account_id', models.CharField(blank=True, max_length=120, help_text='Zoho Books account_id for VAT on payment charges.')),
                ('vat_account_name', models.CharField(blank=True, max_length=255, help_text='Display label only.')),
                ('gateway_charge_rate', models.DecimalField(decimal_places=4, default=Decimal('2.50'), max_digits=6, help_text='Geidea HPP payment gateway charge rate (%).')),
                ('paylink_charge_rate', models.DecimalField(decimal_places=4, default=Decimal('2.50'), max_digits=6, help_text='Geidea Pay by Link charge rate (%).')),
                ('cod_charge_rate', models.DecimalField(decimal_places=4, default=Decimal('1.60'), max_digits=6, help_text='Geidea POS / card on delivery charge rate (%).')),
                ('gateway_vat_rate', models.DecimalField(decimal_places=4, default=Decimal('5.00'), max_digits=6, help_text='VAT rate applied to gateway charge amount (%).')),
                ('paylink_vat_rate', models.DecimalField(decimal_places=4, default=Decimal('5.00'), max_digits=6, help_text='VAT rate applied to pay-by-link charge amount (%).')),
                ('cod_vat_rate', models.DecimalField(decimal_places=4, default=Decimal('5.00'), max_digits=6, help_text='VAT rate applied to COD charge amount (%).')),
                ('journal_gateway_enabled', models.BooleanField(default=False, help_text='Automatically create journal entries for payment_gateway orders.')),
                ('journal_paylink_enabled', models.BooleanField(default=False, help_text='Automatically create journal entries for pay_by_link orders.')),
                ('journal_cod_enabled', models.BooleanField(default=False, help_text='Automatically create journal entries for card_on_delivery orders.')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Zoho Books Store Config',
                'verbose_name_plural': 'Zoho Books Store Configs',
            },
        ),
    ]
