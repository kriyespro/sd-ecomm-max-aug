"""Store-side AI settings: owner/manager paste up to 10 OpenRouter keys,
used only against OpenRouter's free-tier models, rotated so no single key
gets rate-limited."""

from django import forms
from django.contrib import messages
from django.shortcuts import redirect
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.permissions import OWNER_MANAGER, StoreRoleRequiredMixin
from apps.ai import services as ai_services
from apps.ai.models import MAX_KEYS_PER_STORE, AiProviderKey

from .mixins import ActiveProjectMixin


class AiKeyForm(forms.Form):
    label = forms.CharField(max_length=60, required=False,
                            widget=forms.TextInput(attrs={"placeholder": "Optional label"}))
    api_key = forms.CharField(
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
        help_text="From openrouter.ai/keys. Free — only $0 “:free” models are ever called.",
    )


class _Base(StoreRoleRequiredMixin, ActiveProjectMixin):
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can manage AI keys."


class AiKeysView(_Base, TemplateView):
    template_name = "control/ai/keys.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["keys"] = AiProviderKey.objects.filter(project=self.active_project)
        ctx["max_keys"] = MAX_KEYS_PER_STORE
        ctx["form"] = AiKeyForm()
        return ctx

    def post(self, request, *args, **kwargs):
        form = AiKeyForm(request.POST)
        if form.is_valid():
            try:
                ai_services.add_key(
                    project=self.active_project, api_key=form.cleaned_data["api_key"],
                    label=form.cleaned_data["label"], actor=request.user, request=request,
                )
                messages.success(request, "Key added.")
                return redirect("control:ai_keys")
            except ai_services.AiError as exc:
                form.add_error(None, str(exc))
        ctx = self.get_context_data()
        ctx["form"] = form
        return self.render_to_response(ctx)


class AiKeyToggleView(_Base, View):
    def post(self, request, *args, **kwargs):
        row = ai_services.toggle_key(project=self.active_project, key_id=kwargs["pk"])
        if row:
            messages.success(request, f"{row.label} " + ("enabled." if row.is_active else "disabled."))
        return redirect("control:ai_keys")


class AiKeyDeleteView(_Base, View):
    def post(self, request, *args, **kwargs):
        ai_services.remove_key(project=self.active_project, key_id=kwargs["pk"],
                               actor=request.user, request=request)
        messages.success(request, "Key removed.")
        return redirect("control:ai_keys")
