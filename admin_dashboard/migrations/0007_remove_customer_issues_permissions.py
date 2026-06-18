from django.db import migrations

PERMISSION_CODES = (
    "customer-issues.view",
    "customer-issues.manage",
)


def remove_customer_issues_permissions(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    AdminPermission.objects.filter(code__in=PERMISSION_CODES).delete()


def restore_customer_issues_permissions(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    for code, name, module in (
        ("customer-issues.view", "View customer issues", "customer-issues"),
        ("customer-issues.manage", "Manage customer issues", "customer-issues"),
    ):
        AdminPermission.objects.get_or_create(
            code=code,
            defaults={"name": name, "module": module, "description": ""},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("admin_dashboard", "0006_customer_issues_permissions"),
    ]

    operations = [
        migrations.RunPython(
            remove_customer_issues_permissions,
            restore_customer_issues_permissions,
        ),
    ]
