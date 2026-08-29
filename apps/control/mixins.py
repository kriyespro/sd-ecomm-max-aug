"""Control-panel view mixins."""

from django.contrib import messages
from django.shortcuts import redirect

from apps.core.mixins import ControlAccessMixin
from apps.projects.services import projects_for_user

ACTIVE_PROJECT_SESSION_KEY = "active_project_id"


def get_active_project(request):
    """Resolve the project the staff user is currently operating on.

    Order: the store's own domain -> explicit session choice -> the request's
    resolved project -> the user's sole accessible project. Returns ``None``
    when the user must pick one.
    """
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
        continue. May also raise ``PermissionDenied``. Default: allow."""
        return None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["active_project"] = self.active_project
        return ctx
