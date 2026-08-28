"""Mission Control — platform billing (super admin only)."""

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import ListView, TemplateView, UpdateView, View

from apps.billing import services as billing_svc
from apps.billing.models import (
    BillingSettings,
    CommissionStatus,
    Invoice,
    ManagerCommission,
    Plan,
    Subscription,
)
from apps.core.models import AuditLog
from apps.core.services import record_audit

from apps.core.mixins import PlatformAdminRequiredMixin


class PlanForm(forms.ModelForm):
    class Meta:
        model = Plan
        fields = [
            "name", "code", "tagline", "sort_order", "is_active", "is_public",
            "price_monthly", "price_yearly",
            "commission_monthly_pct", "commission_yearly_pct",
            "max_products", "max_staff", "max_custom_domains", "storage_gb",
            "allow_skin_upload", "remove_platform_branding", "priority_support",
            "transaction_fee_pct", "features",
        ]
        help_texts = {
            "price_monthly": "Retail INR / month.",
            "price_yearly": "Retail INR for 12 months.",
            "max_products": "Blank = unlimited.",
            "features": "JSON list of marketing bullet strings.",
        }


class BillingSettingsForm(forms.ModelForm):
    class Meta:
        model = BillingSettings
        fields = [
            "razorpay_key_id", "razorpay_key_secret", "razorpay_webhook_secret",
            "is_test_mode", "trial_days", "grace_days", "invoice_prefix", "currency",
            "default_commission_monthly_pct", "default_commission_yearly_pct",
        ]
        widgets = {"razorpay_key_secret": forms.PasswordInput(render_value=True)}


class BillingDashboardView(PlatformAdminRequiredMixin, TemplateView):
    template_name = "control/billing/dashboard.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["summary"] = billing_svc.platform_summary()
        ctx["recent_invoices"] = (
            Invoice.objects.select_related("subscription__project", "subscription__plan")
            .order_by("-issued_at")[:12]
        )
        ctx["overdue"] = (
            Invoice.objects.filter(status="open", due_at__lt=timezone.now())
            .select_related("subscription__project")[:10]
        )
        return ctx


class PlanListView(PlatformAdminRequiredMixin, ListView):
    template_name = "control/billing/plans.jinja"
    context_object_name = "plans"
    queryset = Plan.objects.all().order_by("sort_order")


class PlanEditView(PlatformAdminRequiredMixin, UpdateView):
    model = Plan
    form_class = PlanForm
    template_name = "control/_object_form.jinja"
    success_url = reverse_lazy("control:billing_plans")

    def form_valid(self, form):
        resp = super().form_valid(form)
        record_audit(actor=self.request.user, action=AuditLog.Action.UPDATE,
                     target=self.object, request=self.request)
        messages.success(self.request, f"{self.object.name} updated.")
        return resp


class BillingSettingsView(PlatformAdminRequiredMixin, UpdateView):
    form_class = BillingSettingsForm
    template_name = "control/_object_form.jinja"
    success_url = reverse_lazy("control:billing_settings")

    def get_object(self, queryset=None):
        return BillingSettings.load()

    def form_valid(self, form):
        resp = super().form_valid(form)
        record_audit(actor=self.request.user, action=AuditLog.Action.UPDATE,
                     target=self.object, request=self.request)
        messages.success(self.request, "Billing settings saved.")
        return resp


class SubscriptionListView(PlatformAdminRequiredMixin, ListView):
    template_name = "control/billing/subscriptions.jinja"
    context_object_name = "subs"
    paginate_by = 50

    def get_queryset(self):
        qs = Subscription.objects.select_related("project", "plan", "manager").order_by("-created_at")
        status = self.request.GET.get("status")
        return qs.filter(status=status) if status else qs


class CommissionListView(PlatformAdminRequiredMixin, ListView):
    template_name = "control/billing/commissions.jinja"
    context_object_name = "rows"
    paginate_by = 50

    def get_queryset(self):
        qs = (ManagerCommission.objects
              .select_related("manager", "subscription__project", "invoice")
              .order_by("-created_at"))
        status = self.request.GET.get("status")
        return qs.filter(status=status) if status else qs

    def get_context_data(self, **kwargs):
        from django.db.models import Sum
        ctx = super().get_context_data(**kwargs)
        ctx["owed"] = (ManagerCommission.objects
                       .filter(status__in=[CommissionStatus.PENDING, CommissionStatus.APPROVED])
                       .values("manager__username", "manager__email")
                       .annotate(total=Sum("amount")).order_by("-total"))
        return ctx


class CommissionMarkPaidView(PlatformAdminRequiredMixin, View):
    def post(self, request, pk, *args, **kwargs):
        row = get_object_or_404(ManagerCommission, pk=pk)
        row.status = CommissionStatus.PAID
        row.paid_at = timezone.now()
        row.payout_ref = request.POST.get("ref", "").strip()[:120]
        row.save(update_fields=["status", "paid_at", "payout_ref", "updated_at"])
        record_audit(actor=request.user, action=AuditLog.Action.UPDATE, target=row,
                     changes={"status": "paid"}, request=request)
        messages.success(request, f"Commission to {row.manager} marked paid.")
        return redirect("control:billing_commissions")
