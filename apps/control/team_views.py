"""Control-panel: per-store team (who can sign into Mission Control).

Screen gated to owner / manager via ``StoreRoleRequiredMixin``; the finer rules
(only an owner touches owner/manager rows, keep >=1 owner) live in
``apps.accounts.team`` and surface here as messages.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView, View

from apps.accounts import team as team_svc
from apps.accounts.models import Membership
from apps.accounts.permissions import OWNER_MANAGER, StoreRoleRequiredMixin

from .mixins import ActiveProjectMixin


class _TeamBase(StoreRoleRequiredMixin, ActiveProjectMixin):
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can manage the team."

    def _membership(self, pk):
        m = get_object_or_404(Membership.objects.select_related("user"), pk=pk)
        if m.project_id != self.active_project.pk:
            raise Http404
        return m


class TeamListView(_TeamBase, TemplateView):
    template_name = "control/team/team_list.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["members"] = team_svc.team_members(self.active_project)
        ctx["team_roles"] = team_svc.TEAM_ROLES
        ctx["can_grant_privileged"] = team_svc.can_grant_privileged(
            self.request.user, self.active_project
        )
        ctx["me_id"] = self.request.user.pk
        return ctx


class TeamAddView(_TeamBase, View):
    def post(self, request, *args, **kwargs):
        try:
            m = team_svc.add_member(
                actor=request.user, project=self.active_project,
                email=request.POST.get("email", ""),
                role=request.POST.get("role", ""), request=request,
            )
            messages.success(
                request, f"Added {m.user.email} as {m.get_role_display()}."
            )
        except (team_svc.TeamError, PermissionDenied) as exc:
            messages.error(request, str(exc))
        return redirect("control:team")


class TeamRoleView(_TeamBase, View):
    def post(self, request, *args, **kwargs):
        m = self._membership(kwargs["pk"])
        try:
            team_svc.change_role(
                actor=request.user, project=self.active_project,
                membership=m, role=request.POST.get("role", ""), request=request,
            )
            messages.success(request, f"Updated {m.user.email}'s role.")
        except (team_svc.TeamError, PermissionDenied) as exc:
            messages.error(request, str(exc))
        return redirect("control:team")


class TeamRemoveView(_TeamBase, View):
    def post(self, request, *args, **kwargs):
        m = self._membership(kwargs["pk"])
        email = m.user.email
        try:
            team_svc.remove_member(
                actor=request.user, project=self.active_project,
                membership=m, request=request,
            )
            messages.success(request, f"Removed {email} from the team.")
        except (team_svc.TeamError, PermissionDenied) as exc:
            messages.error(request, str(exc))
        return redirect("control:team")
