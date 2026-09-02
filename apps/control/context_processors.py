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
from .navigation import build_breadcrumb, build_nav


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
    can_upload = can_manage and (upload_on or is_platform_admin(user))

    nav = build_nav(
        platform_staff=platform_staff,
        platform_admin=is_platform_admin(user),
        active_project=active,
        can_manage=can_manage,
        can_upload_skin=can_upload,
    )
    crumbs, nav_active_key, nav_active_url = build_breadcrumb(request, nav)

    # The store this Host resolves to (a store's own domain / subdomain), when
    # it differs from the one currently selected — drives a "you're managing a
    # different store" banner so a stale session pick can't silently mislead.
    host_project = getattr(request, "project", None)
    try:
        host_mismatch = (
            host_project is not None
            and active is not None
            and host_project.pk != active.pk
            and available.filter(pk=host_project.pk).exists()
        )
    except Exception:  # noqa: BLE001 - request.project may be a lazy 404
        host_project, host_mismatch = None, False

    return {
        "control_active_project": active,
        "control_host_project": host_project if host_mismatch else None,
        "control_nav": nav,
        "control_breadcrumb": crumbs,
        "control_nav_active_key": nav_active_key,
        "control_nav_active_url": nav_active_url,
        "control_available_projects": available,
        "control_available_count": available.count(),
        "control_is_platform_admin": is_platform_admin(user),
        "control_is_platform_staff": platform_staff,
        "control_platform_scope": platform_scope,
        "control_chrome_theme": _chrome_theme(user, role, platform_scope),
        "control_can_manage_store": can_manage,
        "control_store_role": role,
        "control_can_upload_skin": can_upload,
    }
