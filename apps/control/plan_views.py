"""Mission Control — a store's own plan & billing (owner / manager)."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView, View

from apps.accounts.permissions import OWNER_MANAGER, StoreRoleRequiredMixin
from apps.billing import limits
from apps.billing import services as billing_svc
from apps.billing.models import BillingPeriod, Invoice, Plan
from apps.core.models import AuditLog
from apps.core.services import record_audit

from .mixins import ActiveProjectMixin


class _PlanBase(StoreRoleRequiredMixin, ActiveProjectMixin):
    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can manage billing."

    def check_active_project_access(self, request):
        denied = super().check_active_project_access(request)
        if denied is not None:
            return denied
        # A DGC-managed store's own team doesn't self-serve billing.
        from apps.accounts.permissions import is_platform_staff

        if billing_svc.is_dgc_managed(self.active_project) and not is_platform_staff(request.user):
            raise PermissionDenied(
                "Your plan is managed by your onboarding partner. Contact them to change it."
            )
        return None

    def subscription(self):
        sub = getattr(self.active_project, "subscription", None)
        if sub is None:
            sub = billing_svc.ensure_subscription(self.active_project)
        return sub


class PlanView(_PlanBase, TemplateView):
    template_name = "control/billing/store_plan.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sub = self.subscription()
        ctx["subscription"] = sub
        ctx["current_price"] = sub.current_price()
        ctx["plans"] = Plan.objects.filter(is_active=True, is_public=True).order_by("sort_order")
        ctx["usage"] = limits.usage(self.active_project)
        ctx["invoices"] = sub.invoices.all()[:12]
        ctx["open_invoice"] = sub.invoices.filter(status="open").first()
        return ctx


class PlanChangeView(_PlanBase, View):
    def post(self, request, *args, **kwargs):
        sub = self.subscription()
        plan = get_object_or_404(Plan, pk=request.POST.get("plan"), is_active=True)
        period = request.POST.get("period")
        if period not in (BillingPeriod.MONTHLY, BillingPeriod.YEARLY):
            period = BillingPeriod.MONTHLY
        try:
            billing_svc.change_plan(sub, plan=plan, period=period, actor=request.user)
        except billing_svc.BillingError as exc:
            messages.error(request, str(exc))
            return redirect("control:store_plan")
        record_audit(actor=request.user, project=self.active_project,
                     action=AuditLog.Action.UPDATE, target=sub,
                     changes={"plan": plan.code, "period": period}, request=request)
        messages.success(request, f"Plan changed to {plan.name} ({period}).")
        return redirect("control:store_plan")


class InvoicePayStartView(_PlanBase, View):
    """Returns Razorpay checkout params for the store's open invoice (JSON)."""

    def post(self, request, *args, **kwargs):
        sub = self.subscription()
        invoice = get_object_or_404(Invoice, pk=kwargs["pk"], subscription=sub)
        try:
            params = billing_svc.start_payment(invoice)
        except billing_svc.BillingError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        return JsonResponse({
            "key_id": params["key_id"], "order_id": params["order_id"],
            "amount": params["amount"], "currency": params["currency"],
            "synthetic": params["synthetic"], "invoice": invoice.number,
        })


class InvoicePayConfirmView(_PlanBase, View):
    def post(self, request, *args, **kwargs):
        sub = self.subscription()
        invoice = get_object_or_404(Invoice, pk=kwargs["pk"], subscription=sub)
        try:
            billing_svc.confirm_payment(
                invoice,
                razorpay_payment_id=request.POST.get("razorpay_payment_id", ""),
                razorpay_signature=request.POST.get("razorpay_signature", ""),
            )
        except billing_svc.BillingError as exc:
            messages.error(request, str(exc))
            return redirect("control:store_plan")
        record_audit(actor=request.user, project=self.active_project,
                     action=AuditLog.Action.UPDATE, target=invoice,
                     changes={"status": "paid"}, request=request)
        messages.success(request, f"Invoice {invoice.number} paid — subscription active.")
        return redirect("control:store_plan")
