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


def _chrome_theme(user, store_role_val, platform_scope):
    """One keyword driving the Mission Control colour scheme, by the viewer's
    highest role: superuser / Platform Owner -> platform, Platform Manager
    (a Digital Growth Consultant) -> dgc, then per store role."""
    profile = getattr(user, "profile", None)
    role = getattr(profile, "platform_role", "none")
    if user.is_superuser or role == "platform_owner":
        return "platform"
    if role == "platform_manager":
        return "dgc"
    if platform_scope:
        return "platform"
    if store_role_val == "owner":
        return "owner"
    if store_role_val == "manager":
        return "manager"
    return "staff"


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
    role = store_role(user, active)
    return {
        "control_active_project": active,
        "control_available_projects": available,
        "control_available_count": available.count(),
        "control_is_platform_admin": is_platform_admin(user),
        "control_is_platform_staff": platform_staff,
        "control_platform_scope": platform_scope,
        "control_chrome_theme": _chrome_theme(user, role, platform_scope),
        "control_can_manage_store": can_manage,
        "control_store_role": role,
        "control_can_upload_skin": can_manage and (upload_on or is_platform_admin(user)),
    }
