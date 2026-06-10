from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0027_order_geidea_merchant_ref_unique'),
    ]

    operations = [
        migrations.CreateModel(
            name='ZohoBooksJournalLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('order', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='journal_logs',
                    to='shop.order',
                )),
                ('journal_type', models.CharField(
                    choices=[('payment_charge', 'Payment Charge'), ('vat_charge', 'VAT Charge')],
                    max_length=32,
                )),
                ('payment_method', models.CharField(max_length=32)),
                ('rate_used', models.DecimalField(decimal_places=4, max_digits=8)),
                ('base_amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('journal_amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('journal_date', models.DateField()),
                ('zoho_journal_id', models.CharField(
                    blank=True,
                    max_length=120,
                    help_text='Non-empty = journal created successfully. Empty = creation failed.',
                )),
                ('error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-created_at'],
                'unique_together': {('order', 'journal_type')},
            },
        ),
    ]
