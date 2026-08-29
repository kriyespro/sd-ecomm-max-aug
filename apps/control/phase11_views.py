"""Control-panel: analytics/reports, webhooks, notifications, media library."""

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from apps.analytics import services as analytics
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.media import services as media_svc
from apps.media.models import MediaAsset
from apps.notifications.models import (
    NotificationLog,
    NotificationSettings,
    NotificationTemplate,
)
from apps.webhooks import services as webhooks_svc
from apps.webhooks.models import WebhookDelivery, WebhookEndpoint

from .forms import (
    MediaUploadForm,
    NotificationSettingsForm,
    NotificationTemplateForm,
    WebhookEndpointForm,
)
from .mixins import ActiveProjectMixin

# --- Analytics / reports -------------------------------------

class AnalyticsView(ActiveProjectMixin, TemplateView):
    template_name = "control/analytics/dashboard.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["summary"] = analytics.dashboard_summary(self.active_project)
        return ctx


class ReportsView(ActiveProjectMixin, TemplateView):
    template_name = "control/analytics/reports.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["kinds"] = analytics.REPORT_KINDS
        kind = self.request.GET.get("kind", "sales")
        ctx["kind"] = kind
        params = {}
        if self.request.GET.get("from"):
            params["from"] = self.request.GET["from"]
        if self.request.GET.get("to"):
            params["to"] = self.request.GET["to"]
        ctx["date_from"] = self.request.GET.get("from", "")
        ctx["date_to"] = self.request.GET.get("to", "")
        if kind in analytics.REPORT_KINDS:
            try:
                rows = analytics.report(self.active_project, kind, self._parsed(params))
            except ValueError:
                rows = []
            ctx["rows"] = rows
            ctx["columns"] = list(rows[0].keys()) if rows else []
        return ctx

    @staticmethod
    def _parsed(params):
        from datetime import date
        out = {}
        for k in ("from", "to"):
            if params.get(k):
                try:
                    out[k] = date.fromisoformat(params[k])
                except ValueError:
                    pass
        return out


class ReportExportView(ActiveProjectMixin, View):
    def get(self, request, *args, **kwargs):
        kind = request.GET.get("kind", "sales")
        if kind not in analytics.REPORT_KINDS:
            raise Http404
        params = ReportsView._parsed({k: request.GET.get(k) for k in ("from", "to")})
        rows = analytics.report(self.active_project, kind, params)
        csv_text = analytics.to_csv(rows)
        resp = HttpResponse(csv_text, content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="{kind}-report.csv"'
        return resp


# --- Webhooks -----------------------------------------------

class WebhookListView(ActiveProjectMixin, ListView):
    template_name = "control/webhooks/endpoint_list.jinja"
    context_object_name = "endpoints"

    def get_queryset(self):
        return WebhookEndpoint.objects.filter(project=self.active_project)


class _WebhookForm(ActiveProjectMixin):
    form_class = WebhookEndpointForm
    template_name = "control/_object_form.jinja"
    success_url = reverse_lazy("control:webhooks")

    def get_queryset(self):
        return WebhookEndpoint.objects.filter(project=self.active_project)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.active_project
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit(actor=self.request.user, project=self.active_project,
                     action=AuditLog.Action.CREATE if isinstance(self, CreateView) else AuditLog.Action.UPDATE,
                     target=self.object, request=self.request)
        messages.success(self.request, "Webhook endpoint saved.")
        return response


class WebhookCreateView(_WebhookForm, CreateView):
    pass


class WebhookUpdateView(_WebhookForm, UpdateView):
    pass


class WebhookDeleteView(ActiveProjectMixin, DeleteView):
    template_name = "control/catalog/confirm_delete.jinja"
    success_url = reverse_lazy("control:webhooks")

    def get_queryset(self):
        return WebhookEndpoint.objects.filter(project=self.active_project)


class WebhookDeliveriesView(ActiveProjectMixin, ListView):
    template_name = "control/webhooks/delivery_list.jinja"
    context_object_name = "deliveries"
    paginate_by = 50

    def get_queryset(self):
        qs = WebhookDelivery.objects.filter(project=self.active_project).select_related("endpoint")
        if self.request.GET.get("status"):
            qs = qs.filter(status=self.request.GET["status"])
        return qs


class WebhookRetryView(ActiveProjectMixin, View):
    def post(self, request, *args, **kwargs):
        delivery = get_object_or_404(WebhookDelivery, pk=kwargs["pk"], project=self.active_project)
        webhooks_svc.retry_delivery(delivery)
        messages.success(request, "Delivery retried.")
        return redirect("control:webhook_deliveries")


# --- Notifications -----------------------------------------

class NotificationSettingsView(ActiveProjectMixin, UpdateView):
    form_class = NotificationSettingsForm
    template_name = "control/notifications/settings_form.jinja"
    success_url = reverse_lazy("control:notification_settings")

    def get_object(self, queryset=None):
        obj, _ = NotificationSettings.objects.get_or_create(project=self.active_project)
        return obj

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.active_project
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit(actor=self.request.user, project=self.active_project,
                     action=AuditLog.Action.UPDATE, target=self.object, request=self.request)
        messages.success(self.request, "Notification settings saved.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["templates"] = NotificationTemplate.objects.filter(project=self.active_project)
        ctx["logs"] = NotificationLog.objects.filter(project=self.active_project)[:30]
        return ctx


class _NotifTemplateForm(ActiveProjectMixin):
    form_class = NotificationTemplateForm
    template_name = "control/_object_form.jinja"
    success_url = reverse_lazy("control:notification_settings")

    def get_queryset(self):
        return NotificationTemplate.objects.filter(project=self.active_project)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.active_project
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Template saved.")
        return response


class NotifTemplateCreateView(_NotifTemplateForm, CreateView):
    pass


class NotifTemplateUpdateView(_NotifTemplateForm, UpdateView):
    pass


class NotifTemplateDeleteView(ActiveProjectMixin, DeleteView):
    template_name = "control/catalog/confirm_delete.jinja"
    success_url = reverse_lazy("control:notification_settings")

    def get_queryset(self):
        return NotificationTemplate.objects.filter(project=self.active_project)


# --- Media library ----------------------------------------

class MediaLibraryView(ActiveProjectMixin, ListView):
    template_name = "control/media/library.jinja"
    context_object_name = "assets"
    paginate_by = 48

    def get_queryset(self):
        qs = MediaAsset.objects.filter(project=self.active_project)
        if self.request.GET.get("kind"):
            qs = qs.filter(kind=self.request.GET["kind"])
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(original_name__icontains=q)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = MediaUploadForm()
        ctx["q"] = self.request.GET.get("q", "")
        ctx["kind"] = self.request.GET.get("kind", "")
        return ctx


class MediaUploadView(ActiveProjectMixin, View):
    def post(self, request, *args, **kwargs):
        form = MediaUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Choose a file to upload.")
            return redirect("control:media")
        try:
            media_svc.store_upload(
                project=self.active_project, upload=form.cleaned_data["file"],
                uploaded_by=request.user, folder=form.cleaned_data.get("folder", ""),
                alt=form.cleaned_data.get("alt", ""), title=form.cleaned_data.get("title", ""),
            )
            messages.success(request, "Uploaded.")
        except media_svc.MediaError as exc:
            messages.error(request, str(exc))
        return redirect("control:media")


class MediaDeleteView(ActiveProjectMixin, View):
    def post(self, request, *args, **kwargs):
        asset = get_object_or_404(MediaAsset, pk=kwargs["pk"], project=self.active_project)
        media_svc.delete_asset(asset)
        messages.success(request, "Deleted.")
        return redirect("control:media")
