from django.db import migrations


NEW_PERMISSIONS = [
    ("zoho-books-config.view", "View Zoho Books configs", "zoho-books-config"),
    ("zoho-books-config.manage", "Manage Zoho Books configs", "zoho-books-config"),
]


def seed_zoho_books_config_permissions(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    for code, name, module in NEW_PERMISSIONS:
        AdminPermission.objects.get_or_create(
            code=code,
            defaults={"name": name, "module": module, "description": ""},
        )


def unseed_zoho_books_config_permissions(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    for code, _, _ in NEW_PERMISSIONS:
        AdminPermission.objects.filter(code=code).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("admin_dashboard", "0008_orders_collect_cod_permission"),
    ]

    operations = [
        migrations.RunPython(
            seed_zoho_books_config_permissions,
            unseed_zoho_books_config_permissions,
        ),
    ]
