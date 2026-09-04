"""Control-panel inventory screens, scoped to the active project.

Stock never changes in a view directly — always through apps.inventory.services
so the movement ledger stays complete. GET never mutates.
"""

from django.contrib import messages
from django.db.models import F
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView
from django.views.generic.base import TemplateResponseMixin, View

from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.inventory import services as inv
from apps.inventory.models import InventoryItem, StockMovement, Warehouse

from .forms import InventoryItemForm, StockAdjustForm, WarehouseForm
from .mixins import ActiveProjectMixin

# --- Warehouses ----------------------------------------------------

class WarehouseListView(ActiveProjectMixin, ListView):
    template_name = "control/inventory/warehouse_list.jinja"
    context_object_name = "warehouses"

    def get_queryset(self):
        return Warehouse.objects.filter(project=self.active_project)


class _WarehouseFormView(ActiveProjectMixin):
    form_class = WarehouseForm
    template_name = "control/inventory/warehouse_form.jinja"
    success_url = reverse_lazy("control:warehouse_list")

    def get_queryset(self):
        return Warehouse.objects.filter(project=self.active_project)

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
        messages.success(self.request, "Warehouse saved.")
        return response


class WarehouseCreateView(_WarehouseFormView, CreateView):
    pass


class WarehouseUpdateView(_WarehouseFormView, UpdateView):
    pass


class WarehouseDeleteView(ActiveProjectMixin, DeleteView):
    template_name = "control/catalog/confirm_delete.jinja"
    success_url = reverse_lazy("control:warehouse_list")

    def get_queryset(self):
        return Warehouse.objects.filter(project=self.active_project)

    def form_valid(self, form):
        record_audit(
            actor=self.request.user, project=self.active_project,
            action=AuditLog.Action.DELETE, target=self.get_object(), request=self.request,
        )
        return super().form_valid(form)


# --- Inventory items ----------------------------------------------

class InventoryListView(ActiveProjectMixin, ListView):
    template_name = "control/inventory/item_list.jinja"
    context_object_name = "items"
    paginate_by = 40

    def get_queryset(self):
        qs = (
            InventoryItem.objects.filter(warehouse__project=self.active_project)
            .select_related("warehouse", "product", "variant")
        )
        wh = self.request.GET.get("warehouse", "").strip()
        if wh:
            qs = qs.filter(warehouse_id=wh)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(product__title__icontains=q)
        if self.request.GET.get("low") == "1":
            # available (quantity - reserved) <= low_stock_threshold, in SQL —
            # was materializing the whole (unpaginated) queryset in Python.
            qs = qs.filter(
                low_stock_threshold__gt=0,
                quantity__lte=F("reserved") + F("low_stock_threshold"),
            )
        return qs

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["control/inventory/_item_rows.jinja"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["warehouses"] = Warehouse.objects.filter(project=self.active_project)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["warehouse_id"] = self.request.GET.get("warehouse", "")
        ctx["low_only"] = self.request.GET.get("low") == "1"
        ctx["low_count"] = inv.low_stock_count(self.active_project)
        return ctx


class InventoryItemCreateView(ActiveProjectMixin, CreateView):
    form_class = InventoryItemForm
    template_name = "control/inventory/item_form.jinja"
    success_url = reverse_lazy("control:inventory_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.active_project
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        qty = form.cleaned_data.get("initial_quantity") or 0
        if qty:
            inv.receive_stock(
                item=self.object, quantity=qty, actor=self.request.user, note="Initial stock"
            )
        record_audit(
            actor=self.request.user, project=self.active_project,
            action=AuditLog.Action.CREATE, target=self.object, request=self.request,
        )
        messages.success(self.request, "Inventory record created.")
        return response


class _ItemScopedMixin(ActiveProjectMixin, TemplateResponseMixin, View):
    def get_item(self):
        item = get_object_or_404(
            InventoryItem.objects.select_related("warehouse", "product", "variant"),
            pk=self.kwargs["pk"],
        )
        if item.warehouse.project_id != self.active_project.pk:
            raise Http404
        return item


class InventoryAdjustView(_ItemScopedMixin):
    """GET -> HTMX adjust form. POST -> apply and return the updated row."""

    http_method_names = ["get", "post"]

    def get(self, request, *args, **kwargs):
        item = self.get_item()
        form = StockAdjustForm(initial={
            "new_quantity": item.quantity,
            "low_stock_threshold": item.low_stock_threshold,
        })
        return self.response_class(
            request=request,
            template=["control/inventory/_adjust_form.jinja"],
            context={"item": item, "form": form},
            using=self.template_engine,
        )

    def post(self, request, *args, **kwargs):
        item = self.get_item()
        form = StockAdjustForm(request.POST)
        if not form.is_valid():
            return self.response_class(
                request=request,
                template=["control/inventory/_adjust_form.jinja"],
                context={"item": item, "form": form},
                using=self.template_engine,
            )

        threshold = form.cleaned_data.get("low_stock_threshold")
        if threshold is not None and threshold != item.low_stock_threshold:
            item.low_stock_threshold = threshold
            item.save(update_fields=["low_stock_threshold", "updated_at"])

        movement = inv.adjust_stock(
            item=item,
            new_quantity=form.cleaned_data["new_quantity"],
            actor=request.user,
            note=form.cleaned_data.get("note", ""),
        )
        if movement:
            record_audit(
                actor=request.user, project=self.active_project,
                action=AuditLog.Action.UPDATE, target=item,
                changes={"quantity_delta": movement.quantity_delta}, request=request,
            )
        item.refresh_from_db()
        # OOB row swap updates the table; #modal target receives nothing -> closes.
        return self.response_class(
            request=request,
            template=["control/inventory/_item_row.jinja"],
            context={"item": item, "oob": True},
            using=self.template_engine,
        )


class ItemMovementsView(ActiveProjectMixin, ListView):
    template_name = "control/inventory/movements.jinja"
    context_object_name = "movements"
    paginate_by = 50

    def get_queryset(self):
        self.item = get_object_or_404(
            InventoryItem.objects.select_related("warehouse", "product", "variant"),
            pk=self.kwargs["pk"],
        )
        if self.item.warehouse.project_id != self.active_project.pk:
            raise Http404
        return self.item.movements.select_related("actor")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["item"] = self.item
        return ctx
