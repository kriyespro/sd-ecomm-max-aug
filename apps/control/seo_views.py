"""Control-panel SEO: store defaults, per-path meta overrides, redirects."""

from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.seo.models import Redirect, SeoMeta, SeoSettings

from .forms import RedirectForm, SeoMetaForm, SeoSettingsForm
from .mixins import ActiveProjectMixin


class SeoSettingsView(ActiveProjectMixin, UpdateView):
    form_class = SeoSettingsForm
    template_name = "control/seo/settings_form.jinja"
    success_url = reverse_lazy("control:seo_settings")

    def get_object(self, queryset=None):
        obj, _ = SeoSettings.objects.get_or_create(project=self.active_project)
        return obj

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.active_project
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit(actor=self.request.user, project=self.active_project,
                     action=AuditLog.Action.UPDATE, target=self.object, request=self.request)
        messages.success(self.request, "SEO settings saved.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["redirects"] = Redirect.objects.filter(project=self.active_project)
        ctx["metas"] = SeoMeta.objects.filter(project=self.active_project)
        return ctx


class _SeoScopedForm(ActiveProjectMixin):
    model = None

    def get_queryset(self):
        return self.model.objects.filter(project=self.active_project)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.active_project
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit(
            actor=self.request.user, project=self.active_project,
            action=AuditLog.Action.CREATE if isinstance(self, CreateView) else AuditLog.Action.UPDATE,
            target=self.object, request=self.request,
        )
        messages.success(self.request, "Saved.")
        return response


class RedirectListView(ActiveProjectMixin, ListView):
    template_name = "control/seo/redirect_list.jinja"
    context_object_name = "redirects"

    def get_queryset(self):
        return Redirect.objects.filter(project=self.active_project)


class _RedirectForm(_SeoScopedForm):
    model = Redirect
    form_class = RedirectForm
    template_name = "control/_object_form.jinja"
    success_url = reverse_lazy("control:seo_redirects")


class RedirectCreateView(_RedirectForm, CreateView):
    pass


class RedirectUpdateView(_RedirectForm, UpdateView):
    pass


class RedirectDeleteView(ActiveProjectMixin, DeleteView):
    template_name = "control/catalog/confirm_delete.jinja"
    success_url = reverse_lazy("control:seo_redirects")

    def get_queryset(self):
        return Redirect.objects.filter(project=self.active_project)


class _MetaForm(_SeoScopedForm):
    model = SeoMeta
    form_class = SeoMetaForm
    template_name = "control/_object_form.jinja"
    success_url = reverse_lazy("control:seo_settings")


class SeoMetaCreateView(_MetaForm, CreateView):
    pass


class SeoMetaUpdateView(_MetaForm, UpdateView):
    pass


class SeoMetaDeleteView(ActiveProjectMixin, DeleteView):
    template_name = "control/catalog/confirm_delete.jinja"
    success_url = reverse_lazy("control:seo_settings")

    def get_queryset(self):
        return SeoMeta.objects.filter(project=self.active_project)
