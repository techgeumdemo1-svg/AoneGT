from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('shop', '0021_order_zoho_books_sales_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='payment_status',
            field=models.CharField(
                choices=[
                    ('pending', 'Awaiting payment'),
                    ('paid', 'Paid'),
                    ('not_required', 'Pay on delivery'),
                ],
                default='not_required',
                help_text='Gateway/paylink: pending until paid, then paid. COD/card-on-delivery: not_required.',
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='gateway_reference',
            field=models.CharField(
                blank=True,
                help_text='Payment gateway or pay-by-link transaction reference.',
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='prepaid_credited_amount',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0'),
                help_text='AED credited to user account when prepaid checkout payment succeeded.',
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='credit_applied_on_invoice',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0'),
                help_text='AED deducted from user credit when invoice was created.',
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='credit_refunded_remainder',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0'),
                help_text='Prepaid amount not used on invoice (remains on user credit balance).',
                max_digits=12,
            ),
        ),
        migrations.CreateModel(
            name='AccountCreditLedger',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                (
                    'kind',
                    models.CharField(
                        choices=[
                            ('gateway_payment', 'Payment gateway'),
                            ('paylink_payment', 'Pay by link'),
                            ('invoice_application', 'Applied on invoice'),
                            ('order_cancel', 'Order cancelled'),
                            ('admin_adjustment', 'Admin adjustment'),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    'amount',
                    models.DecimalField(
                        decimal_places=2,
                        help_text='Positive = credit in, negative = debit out.',
                        max_digits=12,
                    ),
                ),
                ('balance_after', models.DecimalField(decimal_places=2, max_digits=12)),
                ('reference', models.CharField(blank=True, max_length=255)),
                ('note', models.CharField(blank=True, max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'order',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='credit_ledger_entries',
                        to='shop.order',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='account_credit_entries',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
