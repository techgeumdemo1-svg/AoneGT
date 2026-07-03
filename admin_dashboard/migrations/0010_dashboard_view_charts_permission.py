from django.db import migrations


NEW_PERMISSION = ("dashboard.view_charts", "View dashboard charts", "dashboard")


def seed_dashboard_view_charts_permission(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    code, name, module = NEW_PERMISSION
    perm, _ = AdminPermission.objects.get_or_create(
        code=code,
        defaults={"name": name, "module": module, "description": ""},
    )

    # Grant charts access to every role that can already view the dashboard,
    # so roles that previously reached /dashboard/charts/ via dashboard.view
    # keep access after it moves behind this dedicated permission.
    dashboard_view = AdminPermission.objects.filter(code="dashboard.view").first()
    if dashboard_view is not None:
        for role in dashboard_view.roles.all():
            role.permissions.add(perm)


def unseed_dashboard_view_charts_permission(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    AdminPermission.objects.filter(code=NEW_PERMISSION[0]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("admin_dashboard", "0009_zoho_books_config_permissions"),
    ]

    operations = [
        migrations.RunPython(
            seed_dashboard_view_charts_permission,
            unseed_dashboard_view_charts_permission,
        ),
    ]
