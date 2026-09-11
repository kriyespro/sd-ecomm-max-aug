"""Store-side WhatsApp connect + status (owner / manager)."""

from django import forms
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import FormView

from apps.accounts.permissions import OWNER_MANAGER, StoreRoleRequiredMixin
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.whatsapp import client
from apps.whatsapp.models import WhatsAppAccount, WhatsAppMessage

from .mixins import ActiveProjectMixin


class WhatsAppConnectForm(forms.ModelForm):
    class Meta:
        model = WhatsAppAccount
        fields = ["phone_number_id", "waba_id", "access_token", "app_secret", "graph_version"]
        widgets = {
            "access_token": forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
            "app_secret": forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
        }
        help_texts = {
            "phone_number_id": "Meta → WhatsApp → API Setup → “Phone number ID”.",
            "waba_id": "The WhatsApp Business Account ID on the same screen.",
            "access_token": "A permanent access token for a system user with "
                            "whatsapp_business_messaging on this number.",
            "app_secret": "Optional. Your Meta app secret — set it to have "
                          "inbound webhooks signature-verified.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in ("access_token", "app_secret"):
            self.fields[f].required = False
        if self.instance.pk and self.instance.access_token:
            self.fields["access_token"].help_text += " A token is saved — leave blank to keep it."

    def clean_phone_number_id(self):
        pn = (self.cleaned_data.get("phone_number_id") or "").strip()
        if pn:
            dup = WhatsAppAccount.objects.filter(phone_number_id=pn).exclude(pk=self.instance.pk)
            if dup.exists():
                raise forms.ValidationError(
                    "This phone number ID is already connected to another store."
                )
        return pn

    def clean_access_token(self):
        tok = self.cleaned_data.get("access_token") or ""
        if not tok and self.instance.pk:
            return self.instance.access_token
        return tok

    def clean_app_secret(self):
        sec = self.cleaned_data.get("app_secret") or ""
        if not sec and self.instance.pk and self.instance.app_secret:
            return self.instance.app_secret
        return sec


class _Base(StoreRoleRequiredMixin, ActiveProjectMixin):
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can manage WhatsApp."

    def get_account(self, create=False):
        qs = WhatsAppAccount.objects.filter(project=self.active_project)
        if create:
            obj, _ = WhatsAppAccount.objects.get_or_create(project=self.active_project)
            return obj
        return qs.first()


class WhatsAppSettingsView(_Base, FormView):
    template_name = "control/whatsapp/settings.jinja"
    form_class = WhatsAppConnectForm
    success_url = reverse_lazy("control:whatsapp")

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["instance"] = self.get_account() or WhatsAppAccount(project=self.active_project)
        return kw

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        account = self.get_account()
        ctx["account"] = account
        ctx["webhook_url"] = self.request.build_absolute_uri(reverse("whatsapp:webhook"))
        ctx["messages_recent"] = (
            WhatsAppMessage.objects.filter(project=self.active_project)[:15]
            if account else []
        )
        return ctx

    def form_valid(self, form):
        account = form.save(commit=False)
        account.project = self.active_project
        # Validate the credentials against Meta before marking active.
        try:
            info = client.fetch_number(account)
            account.display_phone = info["display_phone"]
            account.verified_name = info["verified_name"]
            account.is_active = True
            account.last_error = ""
            ok = True
        except client.WhatsAppError as exc:
            account.is_active = False
            account.last_error = str(exc)[:255]
            ok = False
        account.save()
        record_audit(actor=self.request.user, project=self.active_project,
                     action=AuditLog.Action.UPDATE, target=account,
                     changes={"active": account.is_active}, request=self.request)
        if ok:
            messages.success(self.request,
                             f"WhatsApp connected — {account.display_phone} "
                             f"({account.verified_name}).")
        else:
            messages.error(self.request, f"Could not verify: {account.last_error}")
        return redirect(self.success_url)


class WhatsAppTestView(_Base, View):
    def post(self, request):
        account = self.get_account()
        to = (request.POST.get("to") or "").strip()
        if account is None or not account.ready:
            messages.error(request, "Connect WhatsApp first.")
            return redirect("control:whatsapp")
        if not to:
            messages.error(request, "Enter a number to test.")
            return redirect("control:whatsapp")
        try:
            wamid = client.send_template(account, to=to, name="hello_world", language="en_US")
            WhatsAppMessage.objects.create(
                project=self.active_project, to_number=to, wamid=wamid,
                template_name="hello_world", event="test", status="accepted",
            )
            messages.success(request, f"Sent the hello_world template to {to}.")
        except client.WhatsAppError as exc:
            messages.error(request, f"Test failed: {exc}")
        return redirect("control:whatsapp")


class WhatsAppDisconnectView(_Base, View):
    def post(self, request):
        account = self.get_account()
        if account:
            record_audit(actor=request.user, project=self.active_project,
                         action=AuditLog.Action.DELETE, target=account, request=request)
            account.delete()
            messages.success(request, "WhatsApp disconnected.")
        return redirect("control:whatsapp")
