"""Store-side social auto-share: connect Pinterest / Google Business Profile,
pick a board / location, toggle auto-share (owner / manager)."""

from django import forms
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.permissions import OWNER_MANAGER, StoreRoleRequiredMixin
from apps.catalog.models import Product
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.social import clients, oauth
from apps.social.models import (
    IMPLEMENTED,
    SocialAccount,
    SocialPost,
    SocialProvider,
)
from apps.social.services import load_targets, share_product, store_tokens

from .mixins import ActiveProjectMixin

_STATE_KEY = "social_oauth_state"


class _Base(StoreRoleRequiredMixin, ActiveProjectMixin):
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can manage social sharing."

    def account(self, provider, create=False):
        if create:
            obj, _ = SocialAccount.objects.get_or_create(
                project=self.active_project, provider=provider)
            return obj
        return SocialAccount.objects.filter(
            project=self.active_project, provider=provider).first()

    def _redirect_uri(self, provider):
        return self.request.build_absolute_uri(
            reverse("control:social_callback", args=[provider]))


class CredsForm(forms.Form):
    client_id = forms.CharField(max_length=255)
    client_secret = forms.CharField(max_length=255, widget=forms.PasswordInput(render_value=False))


class SocialSettingsView(_Base, TemplateView):
    template_name = "control/social/settings.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        rows = []
        for provider in IMPLEMENTED:
            acc = self.account(provider)
            rows.append({
                "provider": provider,
                "label": SocialProvider(provider).label,
                "account": acc,
                "redirect_uri": self._redirect_uri(provider),
                "targets": self.request.session.get(f"social_targets_{provider}") or [],
            })
        ctx["rows"] = rows
        ctx["recent"] = SocialPost.objects.filter(
            project=self.active_project).select_related("product")[:15]
        return ctx


class SocialSaveCredsView(_Base, View):
    def post(self, request, provider):
        if provider not in IMPLEMENTED:
            raise Http404
        form = CredsForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Client ID and secret are required.")
            return redirect("control:social")
        acc = self.account(provider, create=True)
        acc.client_id = form.cleaned_data["client_id"].strip()
        acc.client_secret = form.cleaned_data["client_secret"].strip()
        acc.save()
        messages.success(request, "Saved. Now click Connect.")
        return redirect("control:social")


class SocialConnectView(_Base, View):
    def get(self, request, provider):
        acc = self.account(provider)
        if acc is None or not (acc.client_id and acc.client_secret):
            messages.error(request, "Add the client ID and secret first.")
            return redirect("control:social")
        state = oauth.make_state()
        request.session[_STATE_KEY] = f"{provider}:{state}"
        return redirect(oauth.authorize_url(
            provider, client_id=acc.client_id,
            redirect_uri=self._redirect_uri(provider), state=state,
        ))


class SocialCallbackView(_Base, View):
    def get(self, request, provider):
        saved = request.session.pop(_STATE_KEY, "")
        want = f"{provider}:{request.GET.get('state', '')}"
        if request.GET.get("error") or saved != want:
            messages.error(request, "Connection cancelled or expired — try again.")
            return redirect("control:social")
        acc = self.account(provider)
        if acc is None:
            return redirect("control:social")
        try:
            tok = oauth.exchange_code(
                provider, code=request.GET.get("code", ""),
                redirect_uri=self._redirect_uri(provider),
                client_id=acc.client_id, client_secret=acc.client_secret,
            )
            store_tokens(acc, tok)
            targets = load_targets(acc)
        except (oauth.OAuthError, clients.SocialAPIError) as exc:
            acc.last_error = str(exc)[:255]
            acc.save(update_fields=["last_error"])
            messages.error(request, f"Connected, but setup failed: {exc}")
            return redirect("control:social")

        request.session[f"social_targets_{provider}"] = targets
        record_audit(actor=request.user, project=self.active_project,
                     action=AuditLog.Action.UPDATE, target=acc,
                     changes={"connected": True}, request=request)
        messages.success(request, "Connected. Choose where posts should go.")
        return redirect("control:social")


class SocialTargetView(_Base, View):
    def post(self, request, provider):
        acc = self.account(provider)
        if acc is None or not acc.connected:
            return redirect("control:social")
        targets = request.session.get(f"social_targets_{provider}") or []
        chosen = request.POST.get("target_id", "")
        match = next((t for t in targets if t["id"] == chosen), None)
        if match is None:
            messages.error(request, "Pick a board / location.")
            return redirect("control:social")
        acc.target_id = match["id"]
        acc.target_name = match["name"]
        acc.auto_share = bool(request.POST.get("auto_share"))
        acc.is_active = True
        acc.save()
        messages.success(request, f"Posts will go to “{match['name']}”.")
        return redirect("control:social")


class SocialToggleView(_Base, View):
    def post(self, request, provider):
        acc = self.account(provider)
        if acc:
            acc.auto_share = not acc.auto_share
            acc.save(update_fields=["auto_share"])
            messages.success(request, "Auto-share " + ("on." if acc.auto_share else "off."))
        return redirect("control:social")


class SocialDisconnectView(_Base, View):
    def post(self, request, provider):
        acc = self.account(provider)
        if acc:
            record_audit(actor=request.user, project=self.active_project,
                         action=AuditLog.Action.DELETE, target=acc, request=request)
            acc.delete()
            messages.success(request, "Disconnected.")
        return redirect("control:social")


class SocialShareNowView(_Base, View):
    def post(self, request, provider):
        acc = self.account(provider)
        product = get_object_or_404(
            Product, project=self.active_project, pk=request.POST.get("product_id"))
        if acc is None or not acc.ready:
            messages.error(request, "Connect the channel first.")
            return redirect("control:social")
        post = share_product(acc, product)
        if post.status == "posted":
            messages.success(request, f"Shared “{product.title}”. {post.url}")
        else:
            messages.error(request, f"{post.status}: {post.error}")
        return redirect("control:social")
