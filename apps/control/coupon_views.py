"""Control-panel coupons: CRUD + redemption log."""

from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.coupons.models import Coupon

from .forms import CouponForm
from .mixins import ActiveProjectMixin


class CouponListView(ActiveProjectMixin, ListView):
    template_name = "control/coupons/coupon_list.jinja"
    context_object_name = "coupons"

    def get_queryset(self):
        return Coupon.objects.filter(project=self.active_project)


class _CouponForm(ActiveProjectMixin):
    form_class = CouponForm
    template_name = "control/coupons/coupon_form.jinja"
    success_url = reverse_lazy("control:coupon_list")

    def get_queryset(self):
        return Coupon.objects.filter(project=self.active_project)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.active_project
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit(actor=self.request.user, project=self.active_project,
                     action=AuditLog.Action.CREATE if isinstance(self, CreateView) else AuditLog.Action.UPDATE,
                     target=self.object, request=self.request)
        messages.success(self.request, "Coupon saved.")
        return response


class CouponCreateView(_CouponForm, CreateView):
    pass


class CouponUpdateView(_CouponForm, UpdateView):
    pass


class CouponDeleteView(ActiveProjectMixin, DeleteView):
    template_name = "control/catalog/confirm_delete.jinja"
    success_url = reverse_lazy("control:coupon_list")

    def get_queryset(self):
        return Coupon.objects.filter(project=self.active_project)


class CouponRedemptionsView(ActiveProjectMixin, DetailView):
    template_name = "control/coupons/coupon_redemptions.jinja"
    context_object_name = "coupon"

    def get_queryset(self):
        return Coupon.objects.filter(project=self.active_project)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["redemptions"] = self.object.redemptions.select_related("order").order_by("-created_at")
        return ctx
