"""Control-panel payments: per-store provider config + per-order payment actions.

All mutations POST-only and routed through apps.payments.services so Payment /
Refund / PaymentEvent rows and the order state stay consistent.
"""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, TemplateView, UpdateView, View

from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.orders.models import Order
from apps.payments import services as pay
from apps.payments.models import Payment, PaymentProviderConfig

from apps.accounts.permissions import OWNER_MANAGER, StoreRoleRequiredMixin

from .forms import PaymentProviderForm
from .mixins import ActiveProjectMixin


class _PaymentAdminMixin(StoreRoleRequiredMixin):
    """Owner/manager (or platform admin) only — guards payment credentials
    and money movement. Store 'staff' can still run routine order payments."""

    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can manage payments."


# --- provider configuration ------------------------------------------

class ProviderConfigListView(_PaymentAdminMixin, ActiveProjectMixin, ListView):
    template_name = "control/payments/provider_list.jinja"
    context_object_name = "configs"

    def get_queryset(self):
        return PaymentProviderConfig.objects.filter(project=self.active_project)


class _ProviderFormView(_PaymentAdminMixin, ActiveProjectMixin):
    form_class = PaymentProviderForm
    template_name = "control/payments/provider_form.jinja"
    success_url = reverse_lazy("control:payment_providers")

    def get_queryset(self):
        return PaymentProviderConfig.objects.filter(project=self.active_project)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.active_project
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit(
            actor=self.request.user, project=self.active_project,
            action=AuditLog.Action.CREATE if isinstance(self, CreateView) else AuditLog.Action.UPDATE,
            target=self.object, request=self.request,
        )
        messages.success(self.request, "Payment provider saved.")
        return response


class ProviderConfigCreateView(_ProviderFormView, CreateView):
    pass


class ProviderConfigUpdateView(_ProviderFormView, UpdateView):
    pass


# --- per-order payment actions --------------------------------------

class _OrderScoped(ActiveProjectMixin):
    def get_order(self):
        order = get_object_or_404(Order, pk=self.kwargs["pk"])
        if order.project_id != self.active_project.pk:
            raise Http404
        return order

    def get_payment(self, order):
        payment = get_object_or_404(Payment, pk=self.kwargs["payment_pk"], order=order)
        return payment


class OrderInitiatePaymentView(_OrderScoped, View):
    def post(self, request, *args, **kwargs):
        order = self.get_order()
        provider_key = request.POST.get("provider", "").strip()
        try:
            pay.initiate_payment(order=order, provider_key=provider_key, actor=request.user)
            messages.success(request, f"{provider_key} payment initiated.")
        except pay.PaymentError as exc:
            messages.error(request, str(exc))
        return redirect("control:order_detail", pk=order.pk)


class OrderOfflinePaymentView(_OrderScoped, View):
    def post(self, request, *args, **kwargs):
        order = self.get_order()
        provider_key = request.POST.get("provider", "cod").strip()
        collected = request.POST.get("mark_collected") == "1"
        try:
            pay.record_offline_payment(
                order=order, provider_key=provider_key, actor=request.user,
                mark_collected=collected, reference=request.POST.get("reference", "").strip(),
            )
            messages.success(request, "Payment recorded.")
        except pay.PaymentError as exc:
            messages.error(request, str(exc))
        return redirect("control:order_detail", pk=order.pk)


class OrderCapturePaymentView(_OrderScoped, View):
    def post(self, request, *args, **kwargs):
        order = self.get_order()
        payment = self.get_payment(order)
        try:
            pay.capture_payment(payment=payment, actor=request.user,
                                reference=request.POST.get("reference", "").strip())
            messages.success(request, "Payment captured.")
        except pay.PaymentError as exc:
            messages.error(request, str(exc))
        return redirect("control:order_detail", pk=order.pk)


class OrderRefundView(_PaymentAdminMixin, _OrderScoped, View):
    def post(self, request, *args, **kwargs):
        order = self.get_order()
        payment = self.get_payment(order)
        raw = request.POST.get("amount", "").strip()
        amount = None
        if raw:
            try:
                amount = Decimal(raw)
            except InvalidOperation:
                messages.error(request, "Invalid refund amount.")
                return redirect("control:order_detail", pk=order.pk)
        try:
            pay.refund_payment(payment=payment, amount=amount,
                               reason=request.POST.get("reason", "").strip(), actor=request.user)
            messages.success(request, "Refund processed.")
        except pay.PaymentError as exc:
            messages.error(request, str(exc))
        return redirect("control:order_detail", pk=order.pk)


class ReconcileView(_PaymentAdminMixin, ActiveProjectMixin, TemplateView):
    template_name = "control/payments/reconcile.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["mismatches"] = pay.reconcile(self.active_project)
        return ctx
