"""Control-panel shipping: zones + methods CRUD, and per-order shipping actions
(quote/select a method, create shipments, advance shipment status). All
mutations POST-only and routed through apps.shipping.services.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from apps.accounts.permissions import OWNER_MANAGER, StoreRoleRequiredMixin
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.orders.models import Order
from apps.shipping import services as ship
from apps.shipping.models import CourierConfig, Shipment, ShippingMethod, ShippingZone

from .forms import CourierConfigForm, ShippingMethodForm, ShippingZoneForm
from .mixins import ActiveProjectMixin, StoreDataAccessMixin


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
    def get_initial(self):
        # Most stores self-ship locally first -- a ready-made "my city" zone
        # (Surat/Gujarat/India) cuts the blank-form friction; still fully
        # editable, and only applies to a brand-new zone, never an edit.
        initial = super().get_initial()
        initial.setdefault("name", "Surat, Gujarat")
        initial.setdefault("countries", ["IN"])
        initial.setdefault("states", ["Gujarat"])
        initial.setdefault("postal_prefixes", ["394", "395"])
        return initial


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


# --- courier credentials ----------------------------------------

class _CourierAdminMixin(StoreDataAccessMixin, StoreRoleRequiredMixin):
    """Owner/manager (or platform admin) only — same gate as payment
    credentials, since these are API keys with money-moving consequences
    (booking real shipments)."""

    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can manage courier settings."


class CourierConfigListView(_CourierAdminMixin, ActiveProjectMixin, ListView):
    template_name = "control/shipping/courier_list.jinja"
    context_object_name = "configs"

    def get_queryset(self):
        return CourierConfig.objects.filter(project=self.active_project)


class _CourierConfigForm(_CourierAdminMixin, ActiveProjectMixin):
    form_class = CourierConfigForm
    template_name = "control/shipping/courier_form.jinja"
    success_url = reverse_lazy("control:courier_configs")

    def get_queryset(self):
        return CourierConfig.objects.filter(project=self.active_project)

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
        messages.success(self.request, "Courier settings saved.")
        return response


class CourierConfigCreateView(_CourierConfigForm, CreateView):
    pass


class CourierConfigUpdateView(_CourierConfigForm, UpdateView):
    pass


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


class OrderSetShippingAndShipView(_OrderScoped, View):
    """Set the shipping method and book the shipment in one submit — was
    two separate forms/clicks for what's almost always one sequential
    decision on a ready-to-fulfill order. create_shipment() already
    resolves the method + carrier from order.shipping_method when not
    passed explicitly, so this just calls both services back to back."""

    def post(self, request, *args, **kwargs):
        order = self.get_order()
        method = get_object_or_404(
            ShippingMethod, pk=request.POST.get("method"), project=self.active_project
        )
        cod = request.POST.get("cod") == "1"
        try:
            ship.set_order_shipping(order=order, method=method, cod=cod, actor=request.user)
            shipment = ship.create_shipment(
                order=order,
                tracking_number=request.POST.get("tracking_number", "").strip(),
                actor=request.user,
            )
            if shipment.notes:
                # create_shipment() degrades a courier failure to a manual/
                # pending shipment instead of raising — the shipping method
                # was still set correctly, but the booking itself didn't
                # happen, so this can't be a plain success message.
                messages.warning(request, f"Shipping set, but the courier booking failed: {shipment.notes}")
            else:
                messages.success(request, f"Shipping set and shipment created: {method.name}.")
        except ship.ShippingError as exc:
            messages.error(request, str(exc))
        return redirect("control:order_detail", pk=order.pk)


class OrderCreateShipmentView(_OrderScoped, View):
    def post(self, request, *args, **kwargs):
        order = self.get_order()
        shipment = ship.create_shipment(
            order=order,
            carrier=request.POST.get("carrier", "").strip(),
            tracking_number=request.POST.get("tracking_number", "").strip(),
            tracking_url=request.POST.get("tracking_url", "").strip(),
            actor=request.user,
        )
        if shipment.notes:
            messages.warning(request, f"Shipment created, but the courier booking failed: {shipment.notes}")
        else:
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
