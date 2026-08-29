"""Mission Control views (project.md section 6).

All views require an active staff user via ControlAccessMixin. Mutations are
POST-only. HTMX endpoints return HTML partials, never JSON.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import DetailView, TemplateView, View
from django.views.generic.base import TemplateResponseMixin

from apps.accounts.permissions import is_platform_staff
from apps.core.mixins import ControlAccessMixin, PlatformAdminRequiredMixin
from apps.projects.services import projects_for_user

from . import services
from .mixins import ACTIVE_PROJECT_SESSION_KEY, get_active_project

User = get_user_model()


class DashboardView(ControlAccessMixin, TemplateView):
    template_name = "control/dashboard.jinja"

    def get(self, request, *args, **kwargs):
        # This dashboard is the platform overview (global user / project counts).
        # A store user must never land here — with no store in context the
        # sidebar is empty, and the numbers aren't theirs. Route them into a
        # store instead.
        if not is_platform_staff(request.user):
            if get_active_project(request) is not None:
                return redirect("control:order_list")
            if projects_for_user(request.user).exists():
                messages.info(request, "Choose a store to manage.")
                return redirect("control:project_picker")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["stats"] = services.dashboard_stats()
        ctx["activity"] = services.recent_activity()
        return ctx


class ProjectPickerView(ControlAccessMixin, TemplateView):
    template_name = "control/project_picker.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["projects"] = projects_for_user(self.request.user)
        return ctx


class SetProjectView(ControlAccessMixin, View):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        pid = request.POST.get("project")
        allowed = projects_for_user(request.user)
        target = allowed.filter(pk=pid).first() if pid else None
        if target is None:
            return HttpResponseBadRequest("Not an accessible project.")
        request.session[ACTIVE_PROJECT_SESSION_KEY] = target.pk
        return redirect(request.POST.get("next") or "control:product_list")


class StatsCardsView(ControlAccessMixin, TemplateView):
    """HTMX partial — polled by the dashboard every 30s."""

    template_name = "control/partials/_stats_cards.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["stats"] = services.dashboard_stats()
        return ctx


class ActivityFeedView(ControlAccessMixin, TemplateView):
    """HTMX partial — polled by the dashboard."""

    template_name = "control/partials/_activity_feed.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["activity"] = services.recent_activity()
        return ctx


class UserListView(PlatformAdminRequiredMixin, TemplateResponseMixin, View):
    template_name = "control/users.jinja"
    partial_name = "control/partials/_user_rows.jinja"

    def get(self, request, *args, **kwargs):
        query = request.GET.get("q", "")
        users = services.search_users(query)
        template = self.partial_name if request.headers.get("HX-Request") else self.template_name
        return self.response_class(
            request=request,
            template=[template],
            context={"users": users, "q": query},
            using=self.template_engine,
        )


class UserDetailView(PlatformAdminRequiredMixin, DetailView):
    template_name = "control/user_detail.jinja"
    context_object_name = "target"
    queryset = User.objects.select_related("profile")


class _UserRowActionView(PlatformAdminRequiredMixin, TemplateResponseMixin, View):
    template_name = "control/partials/_user_row.jinja"
    http_method_names = ["post"]

    def act(self, request, target):  # pragma: no cover - overridden
        raise NotImplementedError

    def post(self, request, pk, *args, **kwargs):
        target = get_object_or_404(User.objects.select_related("profile"), pk=pk)
        self.act(request, target)
        target.refresh_from_db()
        return self.response_class(
            request=request,
            template=[self.template_name],
            context={"u": target},
            using=self.template_engine,
        )


class UserBanView(_UserRowActionView):
    def act(self, request, target):
        services.set_user_banned(actor=request.user, target=target, banned=True, request=request)


class UserUnbanView(_UserRowActionView):
    def act(self, request, target):
        services.set_user_banned(actor=request.user, target=target, banned=False, request=request)


class ImpersonateView(PlatformAdminRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk, *args, **kwargs):
        target = get_object_or_404(User, pk=pk)
        services.start_impersonation(request=request, target=target)
        return redirect("control:impersonate_active")


class ImpersonateActiveView(TemplateResponseMixin, View):
    """Shown while impersonating. Guarded by the session key only, since the
    active user during impersonation is usually not staff. A real storefront
    replaces this landing page in a later phase."""

    template_name = "control/impersonate_active.jinja"
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        if services.IMPERSONATE_SESSION_KEY not in request.session:
            return redirect("control:dashboard")
        return self.response_class(
            request=request,
            template=[self.template_name],
            context={},
            using=self.template_engine,
        )


class StopImpersonateView(View):
    """No ControlAccessMixin: the active session user during impersonation may
    not be staff. Guarded instead by the presence of the impersonation key."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        if services.IMPERSONATE_SESSION_KEY not in request.session:
            return HttpResponseBadRequest("Not impersonating.")
        original = services.stop_impersonation(request=request)
        if original is None:
            return HttpResponseBadRequest("Not impersonating.")
        return redirect("control:users")
