"""Control-panel: custom domains + DNS verification.

Store owners / managers connect their own hostnames; a domain only routes
traffic once its TXT record is verified.
"""

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView, View

from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.projects import domains as domain_svc
from apps.projects.models import Domain

from .mixins import ActiveProjectMixin


class _DomainAccess(ActiveProjectMixin):
    def _guard(self):
        domain_svc.assert_can_manage_domains(self.request.user, self.active_project)

    def _domain(self, pk):
        domain = get_object_or_404(Domain, pk=pk)
        if domain.project_id != self.active_project.pk:
            raise Http404
        return domain


class DomainListView(_DomainAccess, TemplateView):
    template_name = "control/settings/domains.jinja"

    def get_context_data(self, **kwargs):
        self._guard()
        ctx = super().get_context_data(**kwargs)
        ctx["domains"] = domain_svc.domains_for(self.active_project)
        ctx["app_host"] = self.request.get_host()
        ctx["platform_ip"] = getattr(settings, "PLATFORM_PUBLIC_IP", "") or ""
        return ctx


class DomainAddView(_DomainAccess, View):
    def post(self, request, *args, **kwargs):
        self._guard()
        try:
            domain = domain_svc.add_domain(
                project=self.active_project,
                host=request.POST.get("host", ""),
            )
            record_audit(actor=request.user, project=self.active_project,
                         action=AuditLog.Action.CREATE, target=domain, request=request)
            messages.success(request, f"Added {domain.host}. Add the TXT record, then verify.")
        except domain_svc.DomainError as exc:
            messages.error(request, str(exc))
        return redirect("control:domains")


class DomainVerifyView(_DomainAccess, View):
    def post(self, request, *args, **kwargs):
        self._guard()
        domain = self._domain(kwargs["pk"])
        if domain_svc.verify_domain(domain):
            record_audit(actor=request.user, project=self.active_project,
                         action=AuditLog.Action.UPDATE, target=domain,
                         changes={"verified": True}, request=request)
            messages.success(request, f"{domain.host} is verified and now routing.")
        else:
            messages.error(request, domain.last_check_error or "Could not verify yet.")
        return redirect("control:domains")


class DomainPrimaryView(_DomainAccess, View):
    def post(self, request, *args, **kwargs):
        self._guard()
        domain = self._domain(kwargs["pk"])
        try:
            domain_svc.set_primary(domain=domain)
            messages.success(request, f"{domain.host} is now the primary domain.")
        except domain_svc.DomainError as exc:
            messages.error(request, str(exc))
        return redirect("control:domains")


class DomainDeleteView(_DomainAccess, View):
    def post(self, request, *args, **kwargs):
        self._guard()
        domain = self._domain(kwargs["pk"])
        host = domain.host
        record_audit(actor=request.user, project=self.active_project,
                     action=AuditLog.Action.DELETE, target=domain, request=request)
        domain_svc.remove_domain(domain=domain)
        messages.success(request, f"Removed {host}.")
        return redirect("control:domains")
