from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('admin_dashboard', '0003_cms_faq_and_pages'),
    ]

    operations = [
        migrations.CreateModel(
            name='AdminActivityLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('actor_email', models.EmailField(blank=True, max_length=254)),
                ('category', models.CharField(choices=[('orders', 'Orders'), ('returns', 'Returns'), ('customers', 'Customers'), ('users', 'Admin users'), ('cms', 'CMS'), ('banners', 'Banners'), ('stores', 'Stores'), ('delivery_zones', 'Delivery zones'), ('auth', 'Authentication'), ('system', 'System')], max_length=32)),
                ('action', models.CharField(max_length=64)),
                ('message', models.CharField(max_length=500)),
                ('target_type', models.CharField(blank=True, max_length=32)),
                ('target_id', models.PositiveIntegerField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='admin_activity_logs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Admin activity log',
                'verbose_name_plural': 'Admin activity logs',
                'ordering': ['-created_at'],
            },
        ),
    ]
