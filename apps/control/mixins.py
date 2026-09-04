"""Control-panel view mixins."""

from django.contrib import messages
from django.shortcuts import redirect

from apps.core.mixins import ControlAccessMixin
from apps.projects.services import projects_for_user

ACTIVE_PROJECT_SESSION_KEY = "active_project_id"

# URL names a not-yet-onboarded owner may still reach (the wizard itself, the
# store picker, sign-out).
_ONBOARDING_EXEMPT = {"onboarding", "onboarding_skip", "project_picker", "set_project"}


_UNSET = object()


def get_active_project(request):
    """Resolve the project the staff user is currently operating on.

    Order: the store's own domain -> explicit session choice -> the request's
    resolved project -> the user's sole accessible project. Returns ``None``
    when the user must pick one.

    Memoized on the request: ``ActiveProjectMixin.dispatch`` and the
    ``apps.control.context_processors.control`` context processor each
    resolve this independently on every ``/admin/`` request (the latter on
    HTMX polls too) — without caching that's ``projects_for_user`` plus up to
    three more queries run twice per page load for no reason.
    """
    cached = getattr(request, "_control_active_project", _UNSET)
    if cached is not _UNSET:
        return cached

    result = _resolve_active_project(request)
    request._control_active_project = result
    return result


def _resolve_active_project(request):
    available = projects_for_user(request.user)
    resolved = getattr(request, "project", None)

    # On a store's own domain, Mission Control manages *that* store — the Host
    # wins over a stale session pick from another store.
    if (
        getattr(request, "storefront_host", False)
        and resolved
        and available.filter(pk=resolved.pk).exists()
    ):
        return resolved

    pid = request.session.get(ACTIVE_PROJECT_SESSION_KEY)
    if pid:
        match = available.filter(pk=pid).first()
        if match:
            return match

    if resolved and available.filter(pk=resolved.pk).exists():
        return resolved

    only = list(available[:2])
    if len(only) == 1:
        return only[0]

    return None


class ActiveProjectMixin(ControlAccessMixin):
    """Staff-only AND requires a chosen active project.

    Exposes ``self.active_project`` to the view and template context.
    Redirects to the picker when no project is resolved.
    """

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not (user.is_authenticated and user.is_active and user.is_staff):
            return self.handle_no_permission()

        self.active_project = get_active_project(request)
        if self.active_project is None:
            messages.info(request, "Choose a store to work on.")
            return redirect("control:project_picker")

        denied = self.check_active_project_access(request)
        if denied is not None:
            return denied

        # Skip ControlAccessMixin.dispatch (check already done) -> View.dispatch.
        return super(ControlAccessMixin, self).dispatch(request, *args, **kwargs)

    def check_active_project_access(self, request):
        """Hook for subclasses to run extra checks once ``self.active_project``
        is set. Return an ``HttpResponse`` to short-circuit, or ``None`` to
        continue. May also raise ``PermissionDenied``. Default: the onboarding
        gate — a store owner / manager who hasn't finished the setup wizard is
        sent to it. Platform staff and plain store staff pass straight through.
        Subclasses that override should call ``super()`` first."""
        gate = self._onboarding_redirect(request)
        if gate is not None:
            return gate
        return None

    def _onboarding_redirect(self, request):
        from apps.accounts.permissions import OWNER_MANAGER, has_store_role, is_platform_staff
        from apps.projects.verticals import is_onboarded

        match = getattr(request, "resolver_match", None)
        if match is not None and match.url_name in _ONBOARDING_EXEMPT:
            return None
        user = request.user
        if is_platform_staff(user) or is_onboarded(self.active_project):
            return None
        if not has_store_role(user, self.active_project, OWNER_MANAGER):
            return None
        return redirect("control:onboarding")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["active_project"] = self.active_project
        return ctx
