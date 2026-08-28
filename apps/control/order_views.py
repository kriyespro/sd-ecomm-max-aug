"""Control-panel order management, scoped to the active project.

All mutations are POST-only and routed through apps.orders.services so the
status timeline, stock ledger and audit log stay consistent. GET never mutates.
"""

from django.contrib import messages
from django.db.models import Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import DetailView, ListView, View

from apps.orders import services as orders
from apps.orders.models import Order, OrderStatus, PaymentStatus

from .mixins import ActiveProjectMixin


class _OrderScopedMixin(ActiveProjectMixin):
    def get_order(self):
        order = get_object_or_404(
            Order.objects.select_related("warehouse").prefetch_related("items", "events"),
            pk=self.kwargs["pk"],
        )
        if order.project_id != self.active_project.pk:
            raise Http404
        return order


class OrderListView(ActiveProjectMixin, ListView):
    template_name = "control/orders/order_list.jinja"
    context_object_name = "orders"
    paginate_by = 25

    def get_queryset(self):
        qs = Order.objects.filter(project=self.active_project)
        status = self.request.GET.get("status", "").strip()
        if status:
            qs = qs.filter(status=status)
        pay = self.request.GET.get("payment", "").strip()
        if pay:
            qs = qs.filter(payment_status=pay)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(number__icontains=q) | Q(email__icontains=q))
        return qs

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["control/orders/_order_rows.jinja"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["status"] = self.request.GET.get("status", "")
        ctx["payment"] = self.request.GET.get("payment", "")
        ctx["status_choices"] = OrderStatus.choices
        ctx["payment_choices"] = PaymentStatus.choices
        return ctx


class OrderDetailView(_OrderScopedMixin, DetailView):
    template_name = "control/orders/order_detail.jinja"
    context_object_name = "order"

    def get_object(self, queryset=None):
        return self.get_order()

    def get_context_data(self, **kwargs):
        from apps.payments import services as pay

        ctx = super().get_context_data(**kwargs)
        ctx["allowed_transitions"] = orders.allowed_transitions(self.object)
        ctx["events"] = self.object.events.select_related("actor")
        ctx["payments"] = self.object.payments.prefetch_related("refunds")
        ctx["enabled_providers"] = pay.enabled_provider_configs(self.active_project)

        from apps.shipping import services as ship
        from apps.shipping.models import ShipmentStatus

        ctx["shipping_methods"] = ship.methods_for_order(self.object)
        ctx["shipments"] = self.object.shipments.prefetch_related("events", "items")
        ctx["shipment_statuses"] = ShipmentStatus.choices
        return ctx


class OrderStatusView(_OrderScopedMixin, View):
    def post(self, request, *args, **kwargs):
        order = self.get_order()
        to_status = request.POST.get("to_status", "").strip()
        note = request.POST.get("note", "").strip()
        if to_status not in dict(OrderStatus.choices):
            return HttpResponseBadRequest("Unknown status.")
        try:
            orders.transition_order(order=order, to_status=to_status, actor=request.user, note=note)
            messages.success(request, f"Order moved to {to_status}.")
        except orders.OrderError as exc:
            messages.error(request, str(exc))
        return redirect("control:order_detail", pk=order.pk)


class OrderPaymentView(_OrderScopedMixin, View):
    def post(self, request, *args, **kwargs):
        order = self.get_order()
        reference = request.POST.get("reference", "").strip()
        orders.mark_paid(order=order, actor=request.user, reference=reference)
        messages.success(request, "Order marked paid.")
        return redirect("control:order_detail", pk=order.pk)


class OrderFulfillView(_OrderScopedMixin, View):
    def post(self, request, *args, **kwargs):
        order = self.get_order()
        orders.fulfill_order(order=order, actor=request.user, note=request.POST.get("note", "").strip())
        messages.success(request, "Order fulfilled; stock consumed.")
        return redirect("control:order_detail", pk=order.pk)


class OrderNoteView(_OrderScopedMixin, View):
    def post(self, request, *args, **kwargs):
        order = self.get_order()
        orders.add_admin_note(order=order, text=request.POST.get("text", ""), actor=request.user)
        messages.success(request, "Note added.")
        return redirect("control:order_detail", pk=order.pk)


class OrderShippingView(_OrderScopedMixin, View):
    def post(self, request, *args, **kwargs):
        order = self.get_order()
        order.tracking_number = request.POST.get("tracking_number", "").strip()
        order.courier = request.POST.get("courier", "").strip()
        order.shipping_status = request.POST.get("shipping_status", "").strip()
        order.save(update_fields=["tracking_number", "courier", "shipping_status", "updated_at"])
        messages.success(request, "Shipping details updated.")
        return redirect("control:order_detail", pk=order.pk)
