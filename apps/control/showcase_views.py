"""Mission Control: "Live stores" home-page showcase.

A store owner (or the DGC who manages this store) submits their store from
their own settings screen; a platform admin approves or rejects it from a
platform-wide list, same shape as ``PartnerApplicationListView`` /
``PartnerApplicationReviewView`` in ``apps/control/views.py``.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView, View

from apps.accounts.models import StoreRole
from apps.accounts.permissions import StoreRoleRequiredMixin
from apps.core.mixins import PlatformAdminRequiredMixin
from apps.projects.models import Project

from . import services
from .mixins import ActiveProjectMixin


class StoreShowcaseView(StoreRoleRequiredMixin, ActiveProjectMixin, TemplateView):
    """Store owner's own screen: current status + a Submit button."""

    template_name = "control/showcase/settings.jinja"
    required_store_roles = frozenset({StoreRole.OWNER})
    role_denied_message = "Only the store owner can manage the store's listing."


class StoreShowcaseSubmitView(StoreRoleRequiredMixin, ActiveProjectMixin, View):
    required_store_roles = frozenset({StoreRole.OWNER})
    role_denied_message = "Only the store owner can submit the store for listing."

    def post(self, request, *args, **kwargs):
        try:
            services.submit_project_for_showcase(
                project=self.active_project, actor=request.user, request=request,
            )
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
        else:
            messages.success(request, "Submitted — a platform admin will review it.")
        return redirect("control:store_showcase")


class ShowcaseListView(PlatformAdminRequiredMixin, TemplateView):
    template_name = "control/showcase/list.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        status = self.request.GET.get("status", "")
        ctx["projects"] = services.list_showcase_submissions(status)
        ctx["status"] = status
        ctx["pending_count"] = services.list_showcase_submissions("pending").count()
        return ctx


class ShowcaseReviewView(PlatformAdminRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        project = get_object_or_404(Project, pk=pk)
        try:
            services.review_showcase_submission(
                actor=request.user, project=project,
                decision=request.POST.get("decision", ""),
                note=request.POST.get("note", ""), request=request,
            )
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
        else:
            messages.success(request, f"{project.name}: showcase submission reviewed.")
        return redirect("control:showcase_list")
