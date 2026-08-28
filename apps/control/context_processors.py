"""Template context for the control panel topbar (store switcher)."""

from apps.accounts.permissions import (
    OWNER_MANAGER,
    has_store_role,
    is_platform_admin,
    is_platform_staff,
    store_role,
)
from apps.projects.services import projects_for_user

from .mixins import get_active_project


def control(request):
    if not request.path.startswith("/admin/"):
        return {}
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    active = get_active_project(request)
    available = projects_for_user(user)
    can_manage = has_store_role(user, active, OWNER_MANAGER)
    upload_on = bool(active and (active.feature_flags or {}).get("skin_upload"))
    return {
        "control_active_project": active,
        "control_available_projects": available,
        "control_available_count": available.count(),
        "control_is_platform_admin": is_platform_admin(user),
        "control_is_platform_staff": is_platform_staff(user),
        "control_can_manage_store": can_manage,
        "control_store_role": store_role(user, active),
        "control_can_upload_skin": can_manage and (upload_on or is_platform_admin(user)),
    }
