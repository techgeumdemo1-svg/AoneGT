from django.db import migrations


NEW_PERMISSION = ("orders.collect_cod", "Collect COD payment", "orders")

DELIVERY_BOY_ADD = ("orders.view", "orders.collect_cod")
DELIVERY_BOY_REMOVE = ("orders.update_status",)


def seed_collect_cod_permission(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    AdminRole = apps.get_model("admin_dashboard", "AdminRole")

    code, name, module = NEW_PERMISSION
    perm, _ = AdminPermission.objects.get_or_create(
        code=code,
        defaults={"name": name, "module": module, "description": ""},
    )

    role = AdminRole.objects.filter(name="Delivery Boy").first()
    if role is None:
        return
    role.permissions.add(perm)
    for add_code in DELIVERY_BOY_ADD:
        p = AdminPermission.objects.filter(code=add_code).first()
        if p:
            role.permissions.add(p)
    for remove_code in DELIVERY_BOY_REMOVE:
        p = AdminPermission.objects.filter(code=remove_code).first()
        if p:
            role.permissions.remove(p)


def unseed_collect_cod_permission(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    AdminRole = apps.get_model("admin_dashboard", "AdminRole")

    role = AdminRole.objects.filter(name="Delivery Boy").first()
    perm = AdminPermission.objects.filter(code=NEW_PERMISSION[0]).first()
    if role and perm:
        role.permissions.remove(perm)
    if perm:
        perm.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("admin_dashboard", "0007_remove_customer_issues_permissions"),
    ]

    operations = [
        migrations.RunPython(seed_collect_cod_permission, unseed_collect_cod_permission),
    ]
