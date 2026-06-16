from __future__ import annotations

from typing import Optional

from .models import AdminPermission, AdminRole, AdminUserRole


MODULE_REQUIRED_ACTION = {
    "orders": {"GET": "view", "POST": "update_status", "PATCH": "update_status", "PUT": "update_status"},
    "returns": {"GET": "view", "POST": "manage", "PATCH": "manage", "PUT": "manage", "DELETE": "manage"},
    "reports": {"GET": "view", "POST": "manage"},
    "customers": {"GET": "view", "POST": "manage", "PATCH": "manage"},
    "delivery-zones": {"GET": "view", "POST": "manage", "PATCH": "manage", "DELETE": "manage"},
    "stores": {"GET": "view", "POST": "manage", "PATCH": "manage"},
    "banners": {"GET": "view", "POST": "manage", "PATCH": "manage"},
    "cms": {"GET": "view", "POST": "manage", "PATCH": "manage"},
    "finance": {"GET": "view", "POST": "manage", "PATCH": "manage"},
    "transactions": {"GET": "view"},
    "super-coins": {"GET": "view", "POST": "manage", "PATCH": "manage"},
    "activity-logs": {"GET": "view"},
    "users": {"GET": "view", "POST": "manage", "PATCH": "manage"},
    "dashboard": {"GET": "view"},
}


def resolve_admin_module_from_path(path: str) -> Optional[str]:
    clean = (path or "").strip("/")
    marker = "api/admin/"
    idx = clean.find(marker)
    if idx < 0:
        return None
    tail = clean[idx + len(marker):]
    if not tail:
        return None
    return tail.split("/", 1)[0]


def infer_permission_code(request) -> Optional[str]:
    module = resolve_admin_module_from_path(request.path)
    if not module or module == "auth":
        return None
    action_map = MODULE_REQUIRED_ACTION.get(module)
    if not action_map:
        return None
    method = (request.method or "GET").upper()
    action = action_map.get(method)
    if not action:
        return None
    return f"{module}.{action}"


def get_user_role(user) -> Optional[AdminRole]:
    if not user or not getattr(user, "is_authenticated", False):
        return None
    if getattr(user, "is_superuser", False):
        return None
    binding = AdminUserRole.objects.select_related("role").filter(user=user).first()
    return binding.role if binding else None


def user_has_permission_code(user, permission_code: str) -> bool:
    if not permission_code:
        return True
    if getattr(user, "is_superuser", False):
        return True
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return AdminPermission.objects.filter(
        code=permission_code,
        roles__user_bindings__user=user,
    ).exists()
