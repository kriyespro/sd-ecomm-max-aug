"""Control-panel customers: list, detail, notes/group/block, and groups CRUD."""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
    View,
)

from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.customers import services as cust
from apps.customers.models import Customer, CustomerGroup, Segment

from .forms import CustomerForm, CustomerGroupForm
from .mixins import ActiveProjectMixin


class CustomerListView(ActiveProjectMixin, ListView):
    template_name = "control/customers/customer_list.jinja"
    context_object_name = "customers"
    paginate_by = 30

    def get_queryset(self):
        qs = Customer.objects.filter(project=self.active_project).select_related("group")
        q = self.request.GET.get("q", "").strip()
        if q:
            from django.db.models import Q
            qs = qs.filter(Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
        seg = self.request.GET.get("segment", "").strip()
        if seg:
            qs = qs.filter(segment=seg)
        return qs

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["control/customers/_customer_rows.jinja"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["segment"] = self.request.GET.get("segment", "")
        ctx["segments"] = Segment.choices
        return ctx


class CustomerDetailView(ActiveProjectMixin, DetailView):
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


class CustomerUpdateView(ActiveProjectMixin, UpdateView):
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


class CustomerBlockView(ActiveProjectMixin, View):
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


class CustomerResyncView(ActiveProjectMixin, View):
    def post(self, request, *args, **kwargs):
        customer = get_object_or_404(Customer, pk=kwargs["pk"], project=self.active_project)
        cust.sync_customer_stats(customer)
        messages.success(request, "Stats recalculated.")
        return redirect("control:customer_detail", pk=customer.pk)


# --- Groups ---------------------------------------------------

class GroupListView(ActiveProjectMixin, ListView):
    template_name = "control/customers/group_list.jinja"
    context_object_name = "groups"

    def get_queryset(self):
        return CustomerGroup.objects.filter(project=self.active_project)


class _GroupForm(ActiveProjectMixin):
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


class GroupDeleteView(ActiveProjectMixin, DeleteView):
    template_name = "control/catalog/confirm_delete.jinja"
    success_url = reverse_lazy("control:customer_groups")

    def get_queryset(self):
        return CustomerGroup.objects.filter(project=self.active_project)
