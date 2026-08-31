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


def _is_store_scoped_view(request, default):
    """Is the current view scoped to one store (mixes in ``ActiveProjectMixin``)?

    Platform-wide tools (Stores / Users / Billing / Skins / the platform
    dashboard) are not — they get the platform chrome tint even when the admin
    has a store selected in their session. Falls back to ``default`` when the
    view class can't be resolved (error pages, redirects)."""
    match = getattr(request, "resolver_match", None)
    view_cls = getattr(getattr(match, "func", None), "view_class", None)
    if view_cls is None:
        return default
    from apps.control.mixins import ActiveProjectMixin

    return ActiveProjectMixin in view_cls.__mro__


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
    platform_staff = is_platform_staff(user)
    platform_scope = platform_staff and not _is_store_scoped_view(
        request, default=active is not None
    )
    return {
        "control_active_project": active,
        "control_available_projects": available,
        "control_available_count": available.count(),
        "control_is_platform_admin": is_platform_admin(user),
        "control_is_platform_staff": platform_staff,
        "control_platform_scope": platform_scope,
        "control_can_manage_store": can_manage,
        "control_store_role": store_role(user, active),
        "control_can_upload_skin": can_manage and (upload_on or is_platform_admin(user)),
    }
