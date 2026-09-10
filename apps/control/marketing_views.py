"""Store-side marketing / conversion tracking config (owner, manager, DGC)."""

import re

from django import forms
from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from apps.accounts.permissions import OWNER_MANAGER, StoreRoleRequiredMixin
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.marketing.models import TrackingIntegration, TrackingProvider

from .mixins import ActiveProjectMixin

_PIXEL_ID_RE = re.compile(r"^\d{5,20}$")


class TrackingIntegrationForm(forms.ModelForm):
    class Meta:
        model = TrackingIntegration
        fields = ["provider", "pixel_id", "server_token", "test_event_code",
                  "track_browser", "track_server", "is_enabled"]
        widgets = {
            "server_token": forms.PasswordInput(
                render_value=False, attrs={"autocomplete": "off"}
            ),
        }
        help_texts = {
            "pixel_id": "Meta: your Pixel ID from Events Manager (a number).",
            "server_token": "Conversions API access token (Events Manager → "
                            "Settings → Conversions API → Generate access token). "
                            "Enables server-side Purchase events.",
            "test_event_code": "Optional. From the Test Events tab — events sent "
                               "with it only show there, not in live data.",
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        # Meta is the only provider wired today.
        self.fields["provider"].choices = [
            (TrackingProvider.META, TrackingProvider.META.label)
        ]
        self.fields["server_token"].required = False
        if self.instance.pk:
            self.fields["provider"].disabled = True
            if self.instance.server_token:
                self.fields["server_token"].help_text += (
                    "  A token is saved — leave blank to keep it."
                )

    def clean_pixel_id(self):
        pid = (self.cleaned_data.get("pixel_id") or "").strip()
        if pid and not _PIXEL_ID_RE.match(pid):
            raise forms.ValidationError("A Meta Pixel ID is 5–20 digits.")
        return pid

    def clean_server_token(self):
        # Blank on edit = keep the stored token.
        token = self.cleaned_data.get("server_token") or ""
        if not token and self.instance.pk:
            return self.instance.server_token
        return token

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_enabled") and not cleaned.get("pixel_id"):
            self.add_error("pixel_id", "Add the Pixel ID before enabling tracking.")
        if cleaned.get("track_server") and cleaned.get("is_enabled") and not cleaned.get("server_token"):
            self.add_error(
                "server_token",
                "Server-side events need an access token — add one, or turn off "
                "“Send server events”.",
            )
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.project is not None:
            obj.project = self.project
        if commit:
            obj.save()
        return obj


class _TrackingBase(StoreRoleRequiredMixin, ActiveProjectMixin):
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can manage tracking."
    model = TrackingIntegration

    def get_queryset(self):
        return TrackingIntegration.objects.filter(project=self.active_project)


class TrackingListView(_TrackingBase, ListView):
    template_name = "control/marketing/tracking.jinja"
    context_object_name = "integrations"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["can_add_meta"] = not self.get_queryset().filter(
            provider=TrackingProvider.META
        ).exists()
        return ctx


class _TrackingForm(_TrackingBase):
    form_class = TrackingIntegrationForm
    template_name = "control/marketing/tracking_form.jinja"
    success_url = reverse_lazy("control:tracking")

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["project"] = self.active_project
        return kw

    def form_valid(self, form):
        resp = super().form_valid(form)
        record_audit(actor=self.request.user, project=self.active_project,
                     action=AuditLog.Action.UPDATE, target=self.object,
                     changes={"provider": self.object.provider,
                              "enabled": self.object.is_enabled},
                     request=self.request)
        messages.success(self.request, "Tracking settings saved.")
        return resp


class TrackingCreateView(_TrackingForm, CreateView):
    def get_initial(self):
        return {"provider": TrackingProvider.META}


class TrackingUpdateView(_TrackingForm, UpdateView):
    pass


class TrackingDeleteView(_TrackingBase, DeleteView):
    success_url = reverse_lazy("control:tracking")

    def form_valid(self, form):
        record_audit(actor=self.request.user, project=self.active_project,
                     action=AuditLog.Action.DELETE, target=self.get_object(),
                     request=self.request)
        messages.success(self.request, "Tracking integration removed.")
        return super().form_valid(form)
