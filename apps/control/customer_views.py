"""Control-panel customers: list, detail, notes/group/block, and groups CRUD."""

import csv

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
    View,
)

from apps.accounts.permissions import OWNER_MANAGER, StoreRoleRequiredMixin
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.customers import services as cust
from apps.customers.models import Customer, CustomerGroup, Segment

from .forms import CustomerForm, CustomerGroupForm
from .mixins import BULK_ACTION_MAX_ROWS, ActiveProjectMixin, StoreDataAccessMixin


def _filtered_customers(project, params):
    qs = Customer.objects.filter(project=project).select_related("group")
    q = (params.get("q") or "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
    seg = (params.get("segment") or "").strip()
    if seg:
        qs = qs.filter(segment=seg)
    return qs


class CustomerListView(StoreDataAccessMixin, ActiveProjectMixin, ListView):
    template_name = "control/customers/customer_list.jinja"
    context_object_name = "customers"
    paginate_by = 30

    def get_queryset(self):
        return _filtered_customers(self.active_project, self.request.GET)

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["control/customers/_customer_rows.jinja"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        from apps.accounts.permissions import has_store_role

        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["segment"] = self.request.GET.get("segment", "")
        ctx["segments"] = Segment.choices
        ctx["can_export_customers"] = has_store_role(
            self.request.user, self.active_project, OWNER_MANAGER
        )
        ctx["groups"] = CustomerGroup.objects.filter(project=self.active_project)
        return ctx


_EXPORT_COLUMNS = [
    "email", "first_name", "last_name", "phone", "segment",
    "orders_count", "total_spent", "marketing_opt_in", "is_blocked", "created_at",
]


class CustomerExportView(StoreDataAccessMixin, StoreRoleRequiredMixin, ActiveProjectMixin, View):
    """CSV of the (filtered) customer list — orders already had this,
    customers didn't. Store owner / manager only, same gate as order export."""

    required_store_roles = OWNER_MANAGER
    role_denied_message = "Only the store owner or a manager can export customers."

    def get(self, request, *args, **kwargs):
        project = self.active_project
        qs = _filtered_customers(project, request.GET).order_by("-created_at")

        resp = HttpResponse(content_type="text/csv")
        slug = project.slug or f"store-{project.pk}"
        resp["Content-Disposition"] = (
            f'attachment; filename="{slug}-customers-{timezone.now():%Y%m%d}.csv"'
        )
        w = csv.writer(resp)
        w.writerow([*_EXPORT_COLUMNS, "group"])
        for c in qs.iterator(chunk_size=500):
            row = []
            for col in _EXPORT_COLUMNS:
                v = getattr(c, col, "")
                if col == "created_at" and v:
                    v = v.strftime("%Y-%m-%d %H:%M")
                row.append("" if v is None else v)
            row.append(c.group.name if c.group_id else "")
            w.writerow(row)
        return resp


class CustomerBulkGroupAssignView(StoreDataAccessMixin, ActiveProjectMixin, View):
    """Move many existing customers into a group in one submit — was
    one-at-a-time via the customer detail form. Empty group value clears
    the group (same as picking the blank option on the single-customer
    form)."""

    def post(self, request, *args, **kwargs):
        pks = request.POST.getlist("pks")
        group_id = (request.POST.get("group") or "").strip()
        if not pks:
            messages.error(request, "Pick at least one customer.")
            return redirect("control:customers")
        if len(pks) > BULK_ACTION_MAX_ROWS:
            messages.error(request, f"Select {BULK_ACTION_MAX_ROWS} or fewer at a time.")
            return redirect("control:customers")

        group = None
        if group_id:
            group = get_object_or_404(CustomerGroup, pk=group_id, project=self.active_project)
        count = Customer.objects.filter(project=self.active_project, pk__in=pks).update(group=group)
        record_audit(actor=request.user, project=self.active_project, action=AuditLog.Action.UPDATE,
                     target=None, changes={"bulk_group_assign": group.name if group else "", "count": count, "pks": pks},
                     request=request)
        messages.success(request, f"{count} customer(s) moved to {group.name if group else 'no group'}.")
        return redirect("control:customers")


class CustomerDetailView(StoreDataAccessMixin, ActiveProjectMixin, DetailView):
    template_name = "control/customers/customer_detail.jinja"
    context_object_name = "customer"

    def get_object(self, queryset=None):
        obj = get_object_or_404(
            Customer.objects.select_related("group").prefetch_related("addresses"),
            pk=self.kwargs["pk"],
        )
        if obj.project_id != self.active_project.pk:
            raise Http404
        return obj

    def get_context_data(self, **kwargs):
        from apps.orders.models import Order

        ctx = super().get_context_data(**kwargs)
        ctx["orders"] = Order.objects.filter(
            project=self.active_project, email=self.object.email
        ).order_by("-created_at")[:25]
        ctx["form"] = CustomerForm(instance=self.object, project=self.active_project)
        ctx["groups"] = CustomerGroup.objects.filter(project=self.active_project)
        return ctx


class CustomerUpdateView(StoreDataAccessMixin, ActiveProjectMixin, UpdateView):
    """POST-only: the form lives on the customer detail page."""

    form_class = CustomerForm
    http_method_names = ["post"]

    def get_queryset(self):
        return Customer.objects.filter(project=self.active_project)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.active_project
        return kwargs

    def form_valid(self, form):
        self.object = form.save()
        record_audit(actor=self.request.user, project=self.active_project,
                     action=AuditLog.Action.UPDATE, target=self.object, request=self.request)
        messages.success(self.request, "Customer updated.")
        return redirect("control:customer_detail", pk=self.object.pk)

    def form_invalid(self, form):
        messages.error(self.request, "Could not save: " + "; ".join(
            f"{k}: {v[0]}" for k, v in form.errors.items()
        ))
        return redirect("control:customer_detail", pk=self.get_object().pk)


class CustomerBlockView(StoreDataAccessMixin, ActiveProjectMixin, View):
    blocked = True

    def post(self, request, *args, **kwargs):
        customer = get_object_or_404(Customer, pk=kwargs["pk"], project=self.active_project)
        cust.set_blocked(customer=customer, blocked=self.blocked, actor=request.user)
        record_audit(actor=request.user, project=self.active_project,
                     action=AuditLog.Action.UPDATE, target=customer,
                     changes={"is_blocked": self.blocked}, request=request)
        messages.success(request, "Customer blocked." if self.blocked else "Customer unblocked.")
        return redirect("control:customer_detail", pk=customer.pk)


class CustomerUnblockView(CustomerBlockView):
    blocked = False


class CustomerResyncView(StoreDataAccessMixin, ActiveProjectMixin, View):
    def post(self, request, *args, **kwargs):
        customer = get_object_or_404(Customer, pk=kwargs["pk"], project=self.active_project)
        cust.sync_customer_stats(customer)
        messages.success(request, "Stats recalculated.")
        return redirect("control:customer_detail", pk=customer.pk)


# --- Groups ---------------------------------------------------

class GroupListView(StoreDataAccessMixin, ActiveProjectMixin, ListView):
    template_name = "control/customers/group_list.jinja"
    context_object_name = "groups"

    def get_queryset(self):
        return CustomerGroup.objects.filter(project=self.active_project)


class _GroupForm(StoreDataAccessMixin, ActiveProjectMixin):
    form_class = CustomerGroupForm
    template_name = "control/customers/group_form.jinja"
    success_url = reverse_lazy("control:customer_groups")

    def get_queryset(self):
        return CustomerGroup.objects.filter(project=self.active_project)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.active_project
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit(actor=self.request.user, project=self.active_project,
                     action=AuditLog.Action.CREATE if isinstance(self, CreateView) else AuditLog.Action.UPDATE,
                     target=self.object, request=self.request)
        messages.success(self.request, "Group saved.")
        return response


class GroupCreateView(_GroupForm, CreateView):
    pass


class GroupUpdateView(_GroupForm, UpdateView):
    pass


class GroupDeleteView(StoreDataAccessMixin, ActiveProjectMixin, DeleteView):
    template_name = "control/catalog/confirm_delete.jinja"
    success_url = reverse_lazy("control:customer_groups")

    def get_queryset(self):
        return CustomerGroup.objects.filter(project=self.active_project)
