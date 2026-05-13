from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Coupon',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('coupon_id', models.CharField(max_length=120)),
                ('couponset_id', models.CharField(blank=True, max_length=120)),
                ('org_id', models.IntegerField(db_index=True)),
                ('coupon_name', models.CharField(blank=True, max_length=255)),
                ('coupon_code', models.CharField(db_index=True, max_length=120)),
                ('description', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=False)),
                ('status', models.CharField(blank=True, max_length=120)),
                ('rule_type', models.CharField(blank=True, max_length=120)),
                ('coupon_type', models.CharField(blank=True, max_length=120)),
                ('show_in_storefront', models.BooleanField(default=False)),
                ('restrict_for_guest_user', models.BooleanField(default=False)),
                ('restrict_for_offline_payments', models.BooleanField(default=False)),
                ('stop_after_this_rule', models.BooleanField(default=False)),
                ('apply_once_per_order', models.BooleanField(default=False)),
                ('type', models.CharField(blank=True, max_length=120)),
                ('duration', models.CharField(blank=True, max_length=120)),
                ('discount_type', models.CharField(blank=True, max_length=120)),
                ('discount_by', models.CharField(blank=True, max_length=120)),
                ('apply_on', models.CharField(blank=True, max_length=120)),
                ('discount_value', models.CharField(blank=True, max_length=120)),
                ('discount_amounts', models.JSONField(blank=True, default=list)),
                ('max_discount_amount', models.CharField(blank=True, max_length=120)),
                ('max_redemption', models.IntegerField(default=0)),
                ('max_redemption_count', models.IntegerField(default=0)),
                ('redemption_count', models.IntegerField(default=0)),
                ('max_redemption_count_per_user', models.IntegerField(default=0)),
                ('max_usage_per_transaction', models.IntegerField(default=0)),
                ('max_discounted_product_count_per_cart', models.CharField(blank=True, max_length=120)),
                ('minimum_order_value', models.DecimalField(blank=True, decimal_places=3, max_digits=15, null=True)),
                ('minimum_order_quantity', models.CharField(blank=True, max_length=120)),
                ('activation_time', models.DateTimeField(blank=True, null=True)),
                ('expiry_at', models.CharField(blank=True, max_length=120)),
                ('expiry_time', models.DateTimeField(blank=True, null=True)),
                ('eligible_products', models.JSONField(blank=True, default=dict)),
                ('buy_products', models.JSONField(blank=True, default=dict)),
                ('get_products', models.JSONField(blank=True, default=dict)),
                ('eligible_customers', models.JSONField(blank=True, default=dict)),
                ('eligible_shipping_zones', models.JSONField(blank=True, default=dict)),
                ('raw_data', models.JSONField(blank=True, default=dict)),
                ('last_synced_at', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'offer_coupon',
                'ordering': ['-last_synced_at', '-created_at'],
                'unique_together': {('coupon_id', 'org_id')},
            },
        ),
        migrations.CreateModel(
            name='CouponUsageLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('user_id', models.IntegerField(db_index=True)),
                ('coupon_id_str', models.CharField(max_length=120)),
                ('coupon_code', models.CharField(db_index=True, max_length=120)),
                ('org_id', models.IntegerField(db_index=True)),
                ('order_id', models.IntegerField(db_index=True)),
                ('discount_amount_applied', models.DecimalField(decimal_places=3, max_digits=15)),
                ('coupon_type', models.CharField(blank=True, max_length=120)),
                ('discount_type', models.CharField(blank=True, max_length=120)),
                ('used_at', models.DateTimeField(auto_now_add=True)),
                ('coupon', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='usage_logs', to='offer.coupon')),
            ],
            options={
                'db_table': 'offer_coupon_usage_log',
                'ordering': ['-used_at'],
            },
        ),
    ]
