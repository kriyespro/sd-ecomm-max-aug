"""Store-side marketing / conversion tracking config (owner, manager, DGC)."""

import re

from django import forms
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from apps.accounts.permissions import OWNER_MANAGER, StoreRoleRequiredMixin
from apps.core.mixins import PlatformAdminRequiredMixin
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.marketing import meta_oauth
from apps.marketing.models import (
    PlatformTrackingSettings,
    TrackingIntegration,
    TrackingProvider,
)
from apps.marketing.providers import IMPLEMENTED

from .mixins import ActiveProjectMixin

_OAUTH_STATE_KEY = "meta_oauth_state"

# provider -> (id regex, human hint, "what the id is", "what the token is")
_PROVIDER_SPEC = {
    TrackingProvider.META: (
        re.compile(r"^\d{5,20}$"), "a 5–20 digit number",
        "Meta Pixel ID (Events Manager → Data sources)",
        "Conversions API access token (Events Manager → Settings → "
        "Conversions API → Generate access token)",
    ),
    TrackingProvider.GA4: (
        re.compile(r"^G-[A-Z0-9]{6,12}$"), "like G-XXXXXXX",
        "GA4 Measurement ID (Admin → Data streams → your web stream)",
        "Measurement Protocol API secret (same screen → Measurement Protocol "
        "API secrets → Create)",
    ),
    TrackingProvider.TIKTOK: (
        re.compile(r"^[A-Z0-9]{10,40}$"), "the ~20-character Pixel Code",
        "TikTok Pixel Code (Events Manager → your pixel → Settings)",
        "Events API access token (same Settings screen → Generate access token)",
    ),
}


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
        labels = {
            "pixel_id": "Pixel / Measurement ID",
            "server_token": "Server access token / API secret",
            "track_browser": "Load the browser pixel on the storefront",
            "track_server": "Also send server-side Purchase events",
            "test_event_code": "Test event code",
        }
        help_texts = {
            "test_event_code": "Optional. Events sent with it show only in the "
                               "vendor's test/debug view, never in live data.",
        }

    def __init__(self, *args, project=None, taken=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        self.fields["server_token"].required = False

        if self.instance.pk:
            self.fields["provider"].disabled = True
            choices = [(self.instance.provider,
                        TrackingProvider(self.instance.provider).label)]
        else:
            choices = [(p, TrackingProvider(p).label)
                       for p in IMPLEMENTED if p not in taken]
        self.fields["provider"].choices = choices

        spec = _PROVIDER_SPEC.get(self._provider())
        if spec:
            self.fields["pixel_id"].help_text = spec[2] + f" — {spec[1]}."
            self.fields["server_token"].help_text = spec[3] + "."
        if self.instance.pk and self.instance.server_token:
            self.fields["server_token"].help_text += " A token is saved — leave blank to keep it."

    def _provider(self):
        if self.instance.pk:
            return self.instance.provider
        return (self.data.get("provider") if self.is_bound
                else self.initial.get("provider")) or TrackingProvider.META

    def clean_pixel_id(self):
        pid = (self.cleaned_data.get("pixel_id") or "").strip()
        provider = self._provider()
        spec = _PROVIDER_SPEC.get(provider)
        if pid and spec and not spec[0].match(pid):
            raise forms.ValidationError(f"That doesn't look like {spec[2]} ({spec[1]}).")
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
            self.add_error("pixel_id", "Add the ID before enabling tracking.")
        if (cleaned.get("track_server") and cleaned.get("is_enabled")
                and not cleaned.get("server_token")):
            self.add_error(
                "server_token",
                "Server-side events need an access token — add one, or turn off "
                "“Also send server-side Purchase events”.",
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
        taken = set(self.get_queryset().values_list("provider", flat=True))
        ctx["can_add"] = bool([p for p in IMPLEMENTED if p not in taken])
        ctx["meta_oauth_ready"] = meta_oauth.is_configured()
        return ctx


class _TrackingForm(_TrackingBase):
    form_class = TrackingIntegrationForm
    template_name = "control/marketing/tracking_form.jinja"
    success_url = reverse_lazy("control:tracking")

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["project"] = self.active_project
        kw["taken"] = set(
            self.get_queryset().values_list("provider", flat=True)
        )
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
        taken = set(self.get_queryset().values_list("provider", flat=True))
        first = next((p for p in IMPLEMENTED if p not in taken), TrackingProvider.META)
        return {"provider": first}


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


# --- "Connect with Meta" OAuth (optional, platform-owned Meta app) ----------

class _MetaOAuthBase(StoreRoleRequiredMixin, ActiveProjectMixin):
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can manage tracking."

    def _redirect_uri(self):
        return self.request.build_absolute_uri(reverse("control:tracking_meta_callback"))


class MetaConnectStartView(_MetaOAuthBase, View):
    def get(self, request):
        if not meta_oauth.is_configured():
            messages.error(request, "“Connect with Meta” isn't available on this platform.")
            return redirect("control:tracking")
        state = meta_oauth.make_state()
        request.session[_OAUTH_STATE_KEY] = state
        return redirect(meta_oauth.auth_url(self._redirect_uri(), state))


class MetaConnectCallbackView(_MetaOAuthBase, View):
    def get(self, request):
        saved = request.session.pop(_OAUTH_STATE_KEY, None)
        if request.GET.get("error"):
            messages.error(request, "Meta connection was cancelled.")
            return redirect("control:tracking")
        if not saved or saved != request.GET.get("state"):
            messages.error(request, "Meta connection expired — try again.")
            return redirect("control:tracking")
        code = request.GET.get("code", "")
        try:
            token = meta_oauth.exchange_code(code, self._redirect_uri())
            pixels = meta_oauth.list_pixels(token)
        except meta_oauth.MetaOAuthError as exc:
            logger_msg = str(exc)[:200]
            messages.error(request, f"Meta connection failed: {logger_msg}")
            return redirect("control:tracking")

        if not pixels:
            messages.warning(request, "Connected, but no Meta Pixel was found on that account.")
            return redirect("control:tracking")

        pixel = pixels[0]
        row, _ = TrackingIntegration.objects.get_or_create(
            project=self.active_project, provider=TrackingProvider.META,
        )
        row.pixel_id = pixel["id"]
        row.server_token = token
        row.track_browser = True
        row.track_server = True
        row.save()
        record_audit(actor=request.user, project=self.active_project,
                     action=AuditLog.Action.UPDATE, target=row,
                     changes={"provider": "meta", "via": "oauth"}, request=request)
        extra = f" ({len(pixels) - 1} more available — edit to switch)" if len(pixels) > 1 else ""
        messages.success(
            request,
            f"Connected Meta Pixel “{pixel['name']}”{extra}. Review, then turn it on.",
        )
        return redirect("control:tracking_edit", pk=row.pk)


# --- Platform marketing-site pixels (superadmin) ---------------------------

class PlatformTrackingForm(forms.ModelForm):
    class Meta:
        model = PlatformTrackingSettings
        fields = ["is_enabled", "meta_pixel_id", "ga4_measurement_id", "tiktok_pixel_id",
                  "meta_capi_token", "meta_test_event_code"]
        widgets = {
            "meta_capi_token": forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
        }
        labels = {
            "is_enabled": "Load these pixels on the marketing site",
            "meta_pixel_id": "Meta Pixel ID",
            "ga4_measurement_id": "GA4 Measurement ID",
            "tiktok_pixel_id": "TikTok Pixel Code",
            "meta_test_event_code": "Meta test event code",
        }
        help_texts = {
            "meta_pixel_id": "A number. Leave blank to skip Meta.",
            "ga4_measurement_id": "Like G-XXXXXXX. Leave blank to skip GA4.",
            "tiktok_pixel_id": "The ~20-character code. Leave blank to skip TikTok.",
            "meta_test_event_code": "Optional. Events sent with it show only in "
                                    "Events Manager's Test Events tab.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["meta_capi_token"].required = False
        if self.instance.pk and self.instance.meta_capi_token:
            self.fields["meta_capi_token"].help_text = (
                "A token is saved — leave blank to keep it."
            )

    def clean_meta_capi_token(self):
        tok = (self.cleaned_data.get("meta_capi_token") or "").strip()
        if not tok and self.instance.pk:
            return self.instance.meta_capi_token
        return tok

    def _check(self, field, provider):
        val = (self.cleaned_data.get(field) or "").strip()
        spec = _PROVIDER_SPEC[provider]
        if val and not spec[0].match(val):
            raise forms.ValidationError(f"That doesn't look right ({spec[1]}).")
        return val

    def clean_meta_pixel_id(self):
        return self._check("meta_pixel_id", TrackingProvider.META)

    def clean_ga4_measurement_id(self):
        return self._check("ga4_measurement_id", TrackingProvider.GA4)

    def clean_tiktok_pixel_id(self):
        return self._check("tiktok_pixel_id", TrackingProvider.TIKTOK)


class PlatformTrackingView(PlatformAdminRequiredMixin, UpdateView):
    form_class = PlatformTrackingForm
    template_name = "control/marketing/platform_tracking_form.jinja"
    success_url = reverse_lazy("control:platform_tracking")

    def get_object(self, queryset=None):
        return PlatformTrackingSettings.load()

    def form_valid(self, form):
        resp = super().form_valid(form)
        record_audit(actor=self.request.user, action=AuditLog.Action.UPDATE,
                     target=self.object, request=self.request)
        messages.success(self.request, "Marketing-site tracking saved.")
        return resp
