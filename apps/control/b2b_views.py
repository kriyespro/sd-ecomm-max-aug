"""Mission Control: B2B / dropship marketplace. Owner-only everywhere — a
manager or staff member never sees these screens or actions."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView, View

from apps.accounts.models import StoreRole
from apps.accounts.permissions import StoreRoleRequiredMixin
from apps.b2b import services as b2b_svc
from apps.b2b.models import B2BListing, B2BOrderLedger
from apps.catalog.models import Product, ProductStatus

from .forms import B2BImportForm, B2BMarkPaidForm, B2BShipForm
from .mixins import ActiveProjectMixin


class _B2BBase(StoreRoleRequiredMixin, ActiveProjectMixin):
    required_store_roles = frozenset({StoreRole.OWNER})
    role_denied_message = "Only the store owner can manage B2B / wholesale."


class B2BSettingsView(_B2BBase, TemplateView):
    template_name = "control/b2b/settings.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["products"] = (
            Product.objects.filter(project=self.active_project, status=ProductStatus.ACTIVE)
            .select_related("b2b_listing")
            .order_by("title")
        )
        return ctx


class B2BSellerEnableView(_B2BBase, View):
    def post(self, request, *args, **kwargs):
        b2b_svc.set_b2b_seller(
            project=self.active_project, enabled=True, actor=request.user, request=request
        )
        messages.success(request, "B2B / wholesale selling turned on. List products below.")
        return redirect("control:b2b_settings")


class B2BSellerDisableView(_B2BBase, View):
    def post(self, request, *args, **kwargs):
        b2b_svc.set_b2b_seller(
            project=self.active_project, enabled=False, actor=request.user, request=request
        )
        messages.success(request, "B2B / wholesale selling turned off. Stores that already imported your products keep them.")
        return redirect("control:b2b_settings")


class B2BListingCreateView(_B2BBase, View):
    """Inline price edit from the product table on the settings screen — one
    row's form posts here with its own product id and price."""

    def post(self, request, *args, **kwargs):
        product = get_object_or_404(Product, pk=request.POST.get("product_id"), project=self.active_project)
        try:
            b2b_svc.create_or_update_listing(
                project=self.active_project, actor=request.user,
                product=product, wholesale_price=request.POST.get("wholesale_price"),
                request=request,
            )
        except b2b_svc.B2BError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{product.title} listed for B2B.")
        return redirect("control:b2b_settings")


class B2BListingToggleView(_B2BBase, View):
    def post(self, request, pk, *args, **kwargs):
        listing = get_object_or_404(B2BListing, pk=pk, seller_project=self.active_project)
        b2b_svc.set_listing_active(
            listing=listing, active=request.POST.get("active") == "1",
            actor=request.user, request=request,
        )
        return redirect("control:b2b_settings")


class B2BMarketplaceView(_B2BBase, TemplateView):
    template_name = "control/b2b/marketplace.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["listings"] = b2b_svc.marketplace_listings(exclude_project=self.active_project)
        ctx["imported_ids"] = b2b_svc.imported_listing_ids(self.active_project)
        ctx["form"] = B2BImportForm()
        return ctx


class B2BImportView(_B2BBase, View):
    def post(self, request, pk, *args, **kwargs):
        listing = get_object_or_404(B2BListing, pk=pk)
        form = B2BImportForm(request.POST)
        if form.is_valid():
            try:
                b2b_svc.import_listing(
                    listing=listing, buyer_project=self.active_project, actor=request.user,
                    markup_pct=form.cleaned_data["markup_pct"], request=request,
                )
            except b2b_svc.B2BError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    f"Imported {listing.product.title} — it's in your catalog as a draft. "
                    f"Set a category and publish it when you're ready.",
                )
        else:
            messages.error(request, "Enter a valid markup percentage.")
        return redirect("control:b2b_marketplace")


class B2BOrdersToFulfillView(_B2BBase, TemplateView):
    """Seller side: dropship orders they need to ship."""

    template_name = "control/b2b/orders.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ledger_rows"] = b2b_svc.orders_to_fulfill(self.active_project)
        ctx["ship_form"] = B2BShipForm()
        ctx["paid_form"] = B2BMarkPaidForm()
        return ctx


class B2BMarkShippedView(_B2BBase, View):
    def post(self, request, pk, *args, **kwargs):
        ledger = get_object_or_404(B2BOrderLedger, pk=pk, seller_project=self.active_project)
        form = B2BShipForm(request.POST)
        if form.is_valid():
            b2b_svc.mark_shipped(
                ledger=ledger, tracking_number=form.cleaned_data.get("tracking_number", ""),
                courier=form.cleaned_data.get("courier", ""), actor=request.user, request=request,
            )
            messages.success(request, "Marked shipped.")
        return redirect("control:b2b_orders")


class B2BPayablesView(_B2BBase, TemplateView):
    """Buyer side: what this store owes other B2B sellers."""

    template_name = "control/b2b/payables.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ledger_rows"] = b2b_svc.payables(self.active_project)
        ctx["paid_form"] = B2BMarkPaidForm()
        return ctx


class B2BMarkPaidView(_B2BBase, View):
    """Either party can mark a ledger row paid from their own screen."""

    def post(self, request, pk, *args, **kwargs):
        ledger = get_object_or_404(B2BOrderLedger, pk=pk)
        if self.active_project.pk not in {ledger.seller_project_id, ledger.buyer_project_id}:
            raise Http404
        form = B2BMarkPaidForm(request.POST)
        payout_ref = form.cleaned_data.get("payout_ref", "") if form.is_valid() else ""
        try:
            b2b_svc.mark_paid(ledger=ledger, payout_ref=payout_ref, actor=request.user, request=request)
        except PermissionDenied as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Marked paid.")
        target = "control:b2b_orders" if self.active_project.pk == ledger.seller_project_id else "control:b2b_payables"
        return redirect(target)
