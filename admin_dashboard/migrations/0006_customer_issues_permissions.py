from django.db import migrations


NEW_PERMISSIONS = [
    ("customer-issues.view", "View customer issues", "customer-issues"),
    ("customer-issues.manage", "Manage customer issues", "customer-issues"),
]

ROLE_CODES_TO_ADD = {
    "Manager": ["customer-issues.view", "customer-issues.manage"],
    "Support": ["customer-issues.view", "customer-issues.manage"],
    "Staff": ["customer-issues.view"],
}


def seed_customer_issues_permissions(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    AdminRole = apps.get_model("admin_dashboard", "AdminRole")

    created = {}
    for code, name, module in NEW_PERMISSIONS:
        perm, _ = AdminPermission.objects.get_or_create(
            code=code,
            defaults={"name": name, "module": module, "description": ""},
        )
        created[code] = perm

    for role_name, codes in ROLE_CODES_TO_ADD.items():
        role = AdminRole.objects.filter(name=role_name).first()
        if role is None:
            continue
        role.permissions.add(*[created[code] for code in codes if code in created])


def unseed_customer_issues_permissions(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    AdminPermission.objects.filter(
        code__in=[code for code, _, _ in NEW_PERMISSIONS],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("admin_dashboard", "0005_rbac_roles_permissions"),
    ]

    operations = [
        migrations.RunPython(seed_customer_issues_permissions, unseed_customer_issues_permissions),
    ]
