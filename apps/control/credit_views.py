"""Marketing -> Store credit (/admin/marketing/credits/): the store owner
turns loyalty on and sets an earn rate, issues gift cards, and sees the
ledger behind every customer's balance -- one screen, same pattern as the
referral program screen."""

from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import UpdateView, View
from django.shortcuts import redirect

from apps.accounts.permissions import OWNER_MANAGER, StoreRoleRequiredMixin
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.credits import services as credits_svc
from apps.credits.models import CreditLedger, GiftCard, LoyaltyProgram, LoyaltyTier

from .mixins import ActiveProjectMixin


class LoyaltyProgramForm(forms.ModelForm):
    class Meta:
        model = LoyaltyProgram
        fields = ["is_active", "earn_percent"]
        labels = {"is_active": "Loyalty rewards are on"}


class CreditSettingsView(StoreRoleRequiredMixin, ActiveProjectMixin, UpdateView):
    form_class = LoyaltyProgramForm
    template_name = "control/marketing/credits.jinja"
    success_url = reverse_lazy("control:credit_settings")
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can manage store credit."

    def get_object(self, queryset=None):
        program, _ = LoyaltyProgram.objects.get_or_create(project=self.active_project)
        return program

    def form_valid(self, form):
        resp = super().form_valid(form)
        record_audit(
            actor=self.request.user, project=self.active_project,
            action=AuditLog.Action.UPDATE, target=self.object,
            changes={"is_active": self.object.is_active, "earn_percent": str(self.object.earn_percent)},
            request=self.request,
        )
        messages.success(self.request, "Loyalty program settings saved.")
        return resp

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        project = self.active_project
        ctx["gift_cards"] = (
            GiftCard.objects.filter(project=project).order_by("-created_at")[:50]
        )
        ctx["recent_ledger"] = (
            CreditLedger.objects.filter(project=project)
            .select_related("account__customer", "order", "gift_card")
            .order_by("-created_at")[:50]
        )
        ctx["loyalty_tiers"] = LoyaltyTier.objects.filter(project=project).order_by("min_spend")
        return ctx


class LoyaltyTierCreateView(StoreRoleRequiredMixin, ActiveProjectMixin, View):
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can manage loyalty tiers."

    def post(self, request):
        project = self.active_project
        name = request.POST.get("name", "").strip()
        try:
            min_spend = Decimal(request.POST.get("min_spend", "0"))
            earn_percent = Decimal(request.POST.get("earn_percent", "0"))
        except InvalidOperation:
            messages.error(request, "Enter valid numbers for spend and earn rate.")
            return redirect("control:credit_settings")
        if not name or min_spend < 0 or earn_percent < 0:
            messages.error(request, "Enter a tier name, a spend threshold, and an earn rate.")
            return redirect("control:credit_settings")

        tier = LoyaltyTier.objects.create(
            project=project, name=name, min_spend=min_spend, earn_percent=earn_percent,
        )
        record_audit(
            actor=request.user, project=project, action=AuditLog.Action.CREATE,
            target=tier, changes={"name": tier.name, "min_spend": str(tier.min_spend)}, request=request,
        )
        messages.success(request, f"Loyalty tier “{tier.name}” added.")
        return redirect("control:credit_settings")


class LoyaltyTierDeleteView(StoreRoleRequiredMixin, ActiveProjectMixin, View):
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can manage loyalty tiers."

    def post(self, request, pk):
        tier = LoyaltyTier.objects.filter(project=self.active_project, pk=pk).first()
        if tier is not None:
            record_audit(
                actor=request.user, project=self.active_project, action=AuditLog.Action.DELETE,
                target=tier, changes={"name": tier.name}, request=request,
            )
            tier.delete()
            messages.success(request, "Loyalty tier removed.")
        return redirect("control:credit_settings")


class GiftCardIssueView(StoreRoleRequiredMixin, ActiveProjectMixin, View):
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can issue gift cards."

    def post(self, request):
        project = self.active_project
        try:
            value = Decimal(request.POST.get("value", "0"))
        except InvalidOperation:
            value = Decimal("0")
        if value <= 0:
            messages.error(request, "Enter a gift card value greater than zero.")
            return redirect("control:credit_settings")

        email = request.POST.get("issued_to_email", "").strip()
        expires_at = None
        raw_expiry = request.POST.get("expires_at", "").strip()
        if raw_expiry:
            parsed = timezone.datetime.fromisoformat(raw_expiry)
            expires_at = timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed

        card = credits_svc.issue_gift_card(
            project=project, value=value, issued_to_email=email, expires_at=expires_at,
            actor=request.user,
        )
        record_audit(
            actor=request.user, project=project, action=AuditLog.Action.CREATE,
            target=card, changes={"value": str(card.value), "code": card.code}, request=request,
        )
        messages.success(request, f"Gift card {card.code} issued for {card.value}.")
        return redirect("control:credit_settings")
