"""Access-control mixins for class-based views.

Mission Control views (project.md section 6) must be staff-only and must never
mutate data on a GET request.
"""

from django.contrib.auth.mixins import AccessMixin


class ControlAccessMixin(AccessMixin):
    """Require an authenticated, active staff user."""

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not (user.is_authenticated and user.is_active and user.is_staff):
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)


class PlatformAdminRequiredMixin(ControlAccessMixin):
    """Superuser or Platform Owner only — platform-wide tooling: billing
    dashboard, user directory, impersonation, bans, skin catalogue. A Platform
    Manager or any store role must never reach these.
    """

    raise_exception = True
    _require_full_admin = True

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not (user.is_authenticated and user.is_active and user.is_staff):
            return self.handle_no_permission()
        profile = getattr(user, "profile", None)
        ok = user.is_superuser or (
            profile
            and (profile.is_platform_admin if self._require_full_admin
                 else profile.is_platform_staff)
        )
        if not ok:
            return self.handle_no_permission()
        return super(ControlAccessMixin, self).dispatch(request, *args, **kwargs)


class PlatformStaffRequiredMixin(PlatformAdminRequiredMixin):
    """Platform Owner OR Platform Manager — store provisioning and each
    manager's own stores/commissions."""

    _require_full_admin = False
