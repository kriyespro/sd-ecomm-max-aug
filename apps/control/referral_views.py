"""Marketing -> Referral program (/admin/marketing/referrals/): the store
owner turns it on, sets a commission rate + cookie window, and manages every
referrer and their commissions from one screen."""

import csv

from django import forms
from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import UpdateView, View

from apps.accounts.permissions import OWNER_MANAGER, StoreRoleRequiredMixin
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.referrals import services as referrals_svc
from apps.referrals.models import CommissionStatus, ReferralCommission, ReferralProgram, Referrer, ReferrerStatus

from .mixins import ActiveProjectMixin


class ReferralProgramForm(forms.ModelForm):
    class Meta:
        model = ReferralProgram
        fields = ["is_active", "commission_type", "commission_value", "cookie_days", "minimum_payout"]
        labels = {"is_active": "Referral program is on"}


class ReferralProgramView(StoreRoleRequiredMixin, ActiveProjectMixin, UpdateView):
    """Settings form at the top, the full referrer + commission table below
    -- one screen, matching the "everything a store owner needs" pattern
    the rest of Marketing uses (e.g. WhatsApp enquiry button)."""

    form_class = ReferralProgramForm
    template_name = "control/marketing/referral_program.jinja"
    success_url = reverse_lazy("control:referral_program")
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can manage the referral program."

    def get_object(self, queryset=None):
        return referrals_svc.get_or_create_program(self.active_project)

    def form_valid(self, form):
        resp = super().form_valid(form)
        record_audit(
            actor=self.request.user, project=self.active_project,
            action=AuditLog.Action.UPDATE, target=self.object,
            changes={"is_active": self.object.is_active}, request=self.request,
        )
        messages.success(self.request, "Referral program settings saved.")
        return resp

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        project = self.active_project

        referrers = (
            Referrer.objects.filter(project=project)
            .select_related("user")
            .annotate(
                click_count=Count("clicks", distinct=True),
                orders=Count("commissions", distinct=True),
                sales=Sum("commissions__order_amount"),
                pending=Sum(
                    "commissions__commission_amount",
                    filter=Q(commissions__status=CommissionStatus.PENDING),
                ),
                approved=Sum(
                    "commissions__commission_amount",
                    filter=Q(commissions__status=CommissionStatus.APPROVED),
                ),
                paid=Sum(
                    "commissions__commission_amount",
                    filter=Q(commissions__status=CommissionStatus.PAID),
                ),
            )
            .order_by("-created_at")
        )
        ctx["referrers"] = referrers
        ctx["total_referrers"] = len(referrers)
        ctx["active_referrers"] = sum(1 for r in referrers if r.status == ReferrerStatus.ACTIVE)

        commissions = ReferralCommission.objects.filter(project=project)
        ctx["total_orders"] = commissions.count()
        ctx["total_sales"] = commissions.aggregate(s=Sum("order_amount"))["s"] or 0
        ctx["commission_due"] = commissions.filter(
            status__in=[CommissionStatus.PENDING, CommissionStatus.APPROVED]
        ).aggregate(s=Sum("commission_amount"))["s"] or 0
        ctx["commission_paid"] = (
            commissions.filter(status=CommissionStatus.PAID)
            .aggregate(s=Sum("commission_amount"))["s"] or 0
        )
        ctx["recent_commissions"] = (
            commissions.select_related("referrer__user", "order").order_by("-created_at")[:50]
        )
        return ctx


class _ReferralCommissionActionView(StoreRoleRequiredMixin, ActiveProjectMixin, View):
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can manage commissions."
    target_status = None

    def post(self, request, pk):
        commission = get_object_or_404(ReferralCommission, pk=pk, project=self.active_project)
        referrals_svc.set_commission_status(commission, self.target_status, actor=request.user)
        messages.success(request, f"Commission marked {commission.get_status_display()}.")
        return redirect("control:referral_program")


class ReferralCommissionApproveView(_ReferralCommissionActionView):
    target_status = CommissionStatus.APPROVED


class ReferralCommissionRejectView(_ReferralCommissionActionView):
    target_status = CommissionStatus.REJECTED


class ReferralCommissionMarkPaidView(_ReferralCommissionActionView):
    target_status = CommissionStatus.PAID


class ReferrerToggleView(StoreRoleRequiredMixin, ActiveProjectMixin, View):
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can manage referrers."

    def post(self, request, pk):
        referrer = get_object_or_404(Referrer, pk=pk, project=self.active_project)
        referrals_svc.set_referrer_status(referrer, not referrer.is_active, actor=request.user)
        messages.success(
            request, f"{referrer.code} {'enabled' if referrer.is_active else 'disabled'}.",
        )
        return redirect("control:referral_program")


class ReferralExportView(StoreRoleRequiredMixin, ActiveProjectMixin, View):
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can export referral data."

    def get(self, request):
        project = self.active_project
        resp = HttpResponse(content_type="text/csv")
        slug = project.slug or f"store-{project.pk}"
        resp["Content-Disposition"] = (
            f'attachment; filename="{slug}-referrals-{timezone.now():%Y%m%d}.csv"'
        )
        w = csv.writer(resp)
        w.writerow([
            "Order", "Referrer code", "Referrer email", "Order amount",
            "Commission", "Status", "Created",
        ])
        qs = (
            ReferralCommission.objects.filter(project=project)
            .select_related("referrer__user", "order")
            .order_by("-created_at")
        )
        for c in qs.iterator(chunk_size=500):
            w.writerow([
                c.order.number, c.referrer.code, c.referrer.user.email,
                c.order_amount, c.commission_amount, c.status,
                c.created_at.strftime("%Y-%m-%d %H:%M"),
            ])
        return resp
