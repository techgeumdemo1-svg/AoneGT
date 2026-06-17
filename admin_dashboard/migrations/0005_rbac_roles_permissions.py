from django.conf import settings
from django.db import migrations, models


DEFAULT_PERMISSIONS = [
    ("dashboard.view", "View dashboard", "dashboard"),
    ("orders.view", "View orders", "orders"),
    ("orders.update_status", "Update order status", "orders"),
    ("returns.view", "View returns", "returns"),
    ("returns.manage", "Manage returns", "returns"),
    ("reports.view", "View reports", "reports"),
    ("reports.manage", "Export/manage reports", "reports"),
    ("customers.view", "View customers", "customers"),
    ("customers.manage", "Manage customers", "customers"),
    ("users.view", "View admin users", "users"),
    ("users.manage", "Manage admin users", "users"),
    ("delivery-zones.view", "View delivery zones", "delivery-zones"),
    ("delivery-zones.manage", "Manage delivery zones", "delivery-zones"),
    ("stores.view", "View stores", "stores"),
    ("stores.manage", "Manage stores", "stores"),
    ("banners.view", "View banners", "banners"),
    ("banners.manage", "Manage banners", "banners"),
    ("cms.view", "View CMS", "cms"),
    ("cms.manage", "Manage CMS", "cms"),
    ("finance.view", "View finance", "finance"),
    ("finance.manage", "Manage finance", "finance"),
    ("transactions.view", "View transactions", "transactions"),
    ("super-coins.view", "View super coins", "super-coins"),
    ("super-coins.manage", "Manage super coins", "super-coins"),
    ("activity-logs.view", "View activity logs", "activity-logs"),
]


def seed_roles_and_permissions(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    AdminRole = apps.get_model("admin_dashboard", "AdminRole")

    created = {}
    for code, name, module in DEFAULT_PERMISSIONS:
        perm, _ = AdminPermission.objects.get_or_create(
            code=code,
            defaults={"name": name, "module": module, "description": ""},
        )
        created[code] = perm

    role_specs = {
        "Manager": [
            "orders.view",
            "orders.update_status",
            "returns.view",
            "returns.manage",
            "reports.view",
            "reports.manage",
            "dashboard.view",
        ],
        "Staff": [
            "orders.view",
            "orders.update_status",
            "dashboard.view",
        ],
        "Delivery Boy": [
            "orders.update_status",
        ],
        "Support": [
            "returns.view",
            "returns.manage",
            "customers.view",
            "dashboard.view",
        ],
    }
    for role_name, codes in role_specs.items():
        role, _ = AdminRole.objects.get_or_create(name=role_name, defaults={"is_system": True})
        role.permissions.set([created[code] for code in codes if code in created])


class Migration(migrations.Migration):
    dependencies = [
        ("admin_dashboard", "0004_admin_activity_log"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminPermission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("module", models.CharField(max_length=64)),
                ("description", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["module", "code"]},
        ),
        migrations.CreateModel(
            name="AdminRole",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=64, unique=True)),
                ("is_system", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("permissions", models.ManyToManyField(blank=True, related_name="roles", to="admin_dashboard.adminpermission")),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="AdminUserRole",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("role", models.ForeignKey(on_delete=models.CASCADE, related_name="user_bindings", to="admin_dashboard.adminrole")),
                ("user", models.OneToOneField(on_delete=models.CASCADE, related_name="admin_role_binding", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.RunPython(seed_roles_and_permissions, migrations.RunPython.noop),
    ]
