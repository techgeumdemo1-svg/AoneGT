from typing import Any, Dict, Optional

from .models import AdminActivityLog


def record_admin_activity(
    request,
    *,
    category: str,
    action: str,
    message: str,
    target_type: str = "",
    target_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> AdminActivityLog:
    user = getattr(request, "user", None)
    actor = user if user and getattr(user, "is_authenticated", False) else None
    return AdminActivityLog.objects.create(
        actor=actor,
        actor_email=(actor.email if actor else "") or "",
        category=category,
        action=action,
        message=(message or "")[:500],
        target_type=(target_type or "")[:32],
        target_id=target_id,
        metadata=metadata or {},
    )
