"""Control-panel shipping: zones + methods CRUD, and per-order shipping actions
(quote/select a method, create shipments, advance shipment status). All
mutations POST-only and routed through apps.shipping.services.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.orders.models import Order
from apps.shipping import services as ship
from apps.shipping.models import Shipment, ShippingMethod, ShippingZone

from .forms import ShippingMethodForm, ShippingZoneForm
from .mixins import ActiveProjectMixin


class _ScopedForm(ActiveProjectMixin):
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
        messages.success(self.request, "Saved.")
        return response


# --- Zones -------------------------------------------------------

class ZoneListView(ActiveProjectMixin, ListView):
    template_name = "control/shipping/zone_list.jinja"
    context_object_name = "zones"

    def get_queryset(self):
        return ShippingZone.objects.filter(project=self.active_project).prefetch_related("methods")


class _ZoneForm(_ScopedForm):
    form_class = ShippingZoneForm
    template_name = "control/shipping/zone_form.jinja"
    success_url = reverse_lazy("control:shipping_zones")

    def get_queryset(self):
        return ShippingZone.objects.filter(project=self.active_project)


class ZoneCreateView(_ZoneForm, CreateView):
    pass


class ZoneUpdateView(_ZoneForm, UpdateView):
    pass


class ZoneDeleteView(ActiveProjectMixin, DeleteView):
    template_name = "control/catalog/confirm_delete.jinja"
    success_url = reverse_lazy("control:shipping_zones")

    def get_queryset(self):
        return ShippingZone.objects.filter(project=self.active_project)


# --- Methods ---------------------------------------------------

class _MethodForm(_ScopedForm):
    form_class = ShippingMethodForm
    template_name = "control/shipping/method_form.jinja"
    success_url = reverse_lazy("control:shipping_zones")

    def get_queryset(self):
        return ShippingMethod.objects.filter(project=self.active_project)


class MethodCreateView(_MethodForm, CreateView):
    pass


class MethodUpdateView(_MethodForm, UpdateView):
    pass


class MethodDeleteView(ActiveProjectMixin, DeleteView):
    template_name = "control/catalog/confirm_delete.jinja"
    success_url = reverse_lazy("control:shipping_zones")

    def get_queryset(self):
        return ShippingMethod.objects.filter(project=self.active_project)


# --- per-order actions ---------------------------------------

class _OrderScoped(ActiveProjectMixin):
    def get_order(self):
        order = get_object_or_404(
            Order.objects.prefetch_related("items", "shipments", "payments"),
            pk=self.kwargs["pk"],
        )
        if order.project_id != self.active_project.pk:
            raise Http404
        return order


class OrderSetShippingView(_OrderScoped, View):
    def post(self, request, *args, **kwargs):
        order = self.get_order()
        method = get_object_or_404(
            ShippingMethod, pk=request.POST.get("method"), project=self.active_project
        )
        cod = request.POST.get("cod") == "1"
        try:
            ship.set_order_shipping(order=order, method=method, cod=cod, actor=request.user)
            messages.success(request, f"Shipping set: {method.name}.")
        except ship.ShippingError as exc:
            messages.error(request, str(exc))
        return redirect("control:order_detail", pk=order.pk)


class OrderCreateShipmentView(_OrderScoped, View):
    def post(self, request, *args, **kwargs):
        order = self.get_order()
        ship.create_shipment(
            order=order,
            carrier=request.POST.get("carrier", "").strip(),
            tracking_number=request.POST.get("tracking_number", "").strip(),
            tracking_url=request.POST.get("tracking_url", "").strip(),
            actor=request.user,
        )
        messages.success(request, "Shipment created.")
        return redirect("control:order_detail", pk=order.pk)


class ShipmentStatusView(_OrderScoped, View):
    def post(self, request, *args, **kwargs):
        order = self.get_order()
        shipment = get_object_or_404(Shipment, pk=self.kwargs["shipment_pk"], order=order)
        status = request.POST.get("status", "").strip()
        try:
            ship.update_shipment_status(
                shipment=shipment, status=status,
                description=request.POST.get("description", "").strip(),
                location=request.POST.get("location", "").strip(),
                actor=request.user,
            )
            messages.success(request, f"Shipment marked {status}.")
        except (ValueError, ship.ShippingError) as exc:
            messages.error(request, str(exc))
        return redirect("control:order_detail", pk=order.pk)
