from django.db import migrations, models


DEFAULT_PAGES = (
    ('terms', 'Terms & Conditions'),
    ('privacy', 'Privacy Policy'),
    ('about', 'About Us'),
    ('shipping', 'Shipping Policy'),
    ('returns', 'Returns Policy'),
    ('contact', 'Contact Us'),
)


def seed_cms_pages(apps, schema_editor):
    CMSPage = apps.get_model('admin_dashboard', 'CMSPage')
    for slug, title in DEFAULT_PAGES:
        CMSPage.objects.get_or_create(
            slug=slug,
            defaults={'title': title, 'content': '', 'is_active': True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('admin_dashboard', '0002_create_adminloginotp_table'),
    ]

    operations = [
        migrations.CreateModel(
            name='FAQ',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('question', models.CharField(max_length=500)),
                ('answer', models.TextField()),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'FAQ',
                'verbose_name_plural': 'FAQs',
                'ordering': ['sort_order', 'id'],
            },
        ),
        migrations.CreateModel(
            name='CMSPage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(max_length=64, unique=True)),
                ('title', models.CharField(max_length=255)),
                ('content', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'CMS page',
                'verbose_name_plural': 'CMS pages',
                'ordering': ['slug'],
            },
        ),
        migrations.RunPython(seed_cms_pages, migrations.RunPython.noop),
    ]
