"""Mission Control — store provisioning (platform owner / platform manager)."""

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import DetailView, FormView, ListView, View

from apps.accounts.models import PlatformRole, StoreRole
from apps.accounts.permissions import is_platform_admin
from apps.billing.models import BillingPeriod, Plan
from apps.core.mixins import PlatformStaffRequiredMixin
from apps.projects import subdomains
from apps.projects.models import Project
from apps.projects.services import projects_for_user

from . import store_services
from .forms import StoreManagerAssignForm
from .mixins import ACTIVE_PROJECT_SESSION_KEY

User = get_user_model()


class _StoreScope(PlatformStaffRequiredMixin):
    def accessible(self):
        return projects_for_user(self.request.user).select_related("subscription__plan")

    def get_store(self, pk):
        store = self.accessible().filter(pk=pk).first()
        if store is None:
            raise Http404
        return store


class StoreCreateForm(forms.Form):
    name = forms.CharField(max_length=120, label="Store name")
    subdomain = forms.CharField(
        required=False, label="Store web address", max_length=subdomains.MAX_LEN,
        widget=forms.TextInput(attrs={"autocapitalize": "none", "autocomplete": "off",
                                      "pattern": "[a-zA-Z0-9-]+", "data-subdomain": "1"}),
    )
    primary_domain = forms.CharField(required=False, label="Custom domain",
                                     help_text="Optional. A domain the owner already "
                                               "owns, e.g. shop.brand.com. Overrides "
                                               "the web address above.")
    currency = forms.CharField(max_length=3, initial="INR")
    country = forms.CharField(max_length=2, initial="IN")

    owner_email = forms.EmailField(label="Owner email")
    owner_name = forms.CharField(required=False, label="Owner name")
    owner_password = forms.CharField(
        required=False, label="Owner password", widget=forms.PasswordInput(render_value=False),
        strip=False,
        help_text="Set a password so a new owner can sign in right away. "
                  "Leave blank for an existing account, or to set it later under Users.",
    )

    plan = forms.ModelChoiceField(queryset=Plan.objects.filter(is_active=True).order_by("sort_order"))
    period = forms.ChoiceField(choices=BillingPeriod.choices, initial=BillingPeriod.YEARLY)
    manager = forms.ModelChoiceField(
        required=False, label="DGC / marketing partner (commission credited here)",
        queryset=User.objects.filter(profile__platform_role=PlatformRole.MANAGER, is_active=True),
    )

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._actor = actor
        # A Platform Manager can only sign a store up under their own name.
        if actor is not None and not is_platform_admin(actor):
            self.fields.pop("manager", None)

        # Default the plan picker to "growth" (falls back to the first plan).
        if not self.is_bound and self.fields["plan"].initial is None:
            self.fields["plan"].initial = (
                self.fields["plan"].queryset.filter(code="growth").first()
                or self.fields["plan"].queryset.first()
            )

        base = subdomains.base_domain()
        if base and "subdomain" in self.fields:
            self.fields["subdomain"].help_text = (
                "Auto-filled from the store name — edit if you like. "
                "The storefront is live here straight away."
            )
        else:
            self.fields.pop("subdomain", None)

    def clean_subdomain(self):
        raw = (self.cleaned_data.get("subdomain") or "").strip()
        if not raw:
            return ""
        slug = subdomains.slugify(raw)
        if len(slug) < 2:
            raise forms.ValidationError("Use at least 2 letters or numbers.")
        if not subdomains.is_available(slug):
            raise forms.ValidationError("That address is taken — try another.")
        return slug

    def clean_owner_password(self):
        pw = self.cleaned_data.get("owner_password") or ""
        if pw:
            validate_password(pw)
        return pw


class StoreListView(_StoreScope, ListView):
    template_name = "control/stores/list.jinja"
    context_object_name = "stores"
    paginate_by = 50

    def get_queryset(self):
        return self.accessible().order_by("name")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["is_admin"] = is_platform_admin(self.request.user)
        return ctx


class StoreCreateView(_StoreScope, FormView):
    template_name = "control/stores/create.jinja"
    form_class = StoreCreateForm

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["actor"] = self.request.user
        return kw

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["base_domain"] = subdomains.base_domain()
        return ctx

    def form_valid(self, form):
        actor = self.request.user
        manager = form.cleaned_data.get("manager")
        if not is_platform_admin(actor):
            # Manager signs the store up under themselves.
            manager = actor if actor.profile.platform_role == PlatformRole.MANAGER else None
        try:
            project, owner, created = store_services.create_store(
                name=form.cleaned_data["name"],
                subdomain=form.cleaned_data.get("subdomain", ""),
                primary_domain=form.cleaned_data["primary_domain"],
                currency=form.cleaned_data["currency"],
                country=form.cleaned_data["country"],
                owner_email=form.cleaned_data["owner_email"],
                owner_name=form.cleaned_data["owner_name"],
                plan=form.cleaned_data["plan"],
                period=form.cleaned_data["period"],
                manager=manager, actor=actor, request=self.request,
                owner_password=form.cleaned_data.get("owner_password") or None,
            )
        except ValidationError as exc:
            for m in exc.messages:
                form.add_error(None, m)
            return self.form_invalid(form)

        if not created:
            note = "their existing login"
        elif form.cleaned_data.get("owner_password"):
            note = "the password you just set"
        else:
            note = "a password you set under Users → the owner → Reset password"
        messages.success(
            self.request,
            f"Store “{project.name}” created. The owner ({owner.email}) signs in with {note}.",
        )
        return redirect("control:store_detail", pk=project.pk)


class StoreDetailView(_StoreScope, DetailView):
    template_name = "control/stores/detail.jinja"
    context_object_name = "store"

    def get_object(self, queryset=None):
        return self.get_store(self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        from apps.core.middleware import trusted_base_url

        ctx = super().get_context_data(**kwargs)
        store = ctx["store"]
        ctx["members"] = (
            store.memberships.filter(is_active=True, role__in=["owner", "manager", "staff"])
            .select_related("user").order_by("role")
        )
        ctx["subscription"] = getattr(store, "subscription", None)
        ctx["role_choices"] = [
            (StoreRole.OWNER, "Owner"), (StoreRole.MANAGER, "Manager"), (StoreRole.STAFF, "Staff"),
        ]
        ctx["is_admin"] = is_platform_admin(self.request.user)
        ctx["is_superuser"] = self.request.user.is_superuser
        if ctx["is_admin"]:
            ctx["manager_form"] = StoreManagerAssignForm(
                initial={"manager": getattr(ctx["subscription"], "manager_id", None)}
            )
            ctx["billing_plans"] = Plan.objects.filter(is_active=True).order_by("sort_order")
            ctx["billing_periods"] = BillingPeriod.choices
            ctx["open_invoice"] = (
                ctx["subscription"].invoices.filter(status="open").first()
                if ctx["subscription"] else None
            )
        ctx["owner_membership"] = (
            store.memberships.filter(role=StoreRole.OWNER, is_active=True)
            .select_related("user").first()
        )
        ctx["store_url"] = trusted_base_url(self.request, store)
        ctx["control_login_url"] = self.request.build_absolute_uri(reverse("control:dashboard"))
        return ctx


class StoreBillingAdjustView(_StoreScope, View):
    """Super-admin: change a store's plan / period, or gift it (comp)."""

    def post(self, request, pk, *args, **kwargs):
        from apps.billing import services as billing_svc

        store = self.get_store(pk)
        if not is_platform_admin(request.user):
            raise PermissionDenied
        sub = getattr(store, "subscription", None)
        if sub is None:
            messages.error(request, "This store has no subscription.")
            return redirect("control:store_detail", pk=pk)

        plan = Plan.objects.filter(pk=request.POST.get("plan"), is_active=True).first()
        period = request.POST.get("period")
        comp = request.POST.get("comp") == "on"
        billing_svc.admin_adjust(
            sub, plan=plan, period=period, comp=comp, actor=request.user,
        )
        messages.success(
            request,
            f"{store.name}: now on {sub.plan.name} ({sub.period})"
            + (" — gifted (free)." if sub.is_comp else "."),
        )
        return redirect("control:store_detail", pk=pk)


class StoreBillingMarkPaidView(_StoreScope, View):
    """Super-admin: record an out-of-band payment (settle open invoice / renew)."""

    def post(self, request, pk, *args, **kwargs):
        from apps.billing import services as billing_svc

        store = self.get_store(pk)
        if not is_platform_admin(request.user):
            raise PermissionDenied
        sub = getattr(store, "subscription", None)
        if sub is None:
            messages.error(request, "This store has no subscription.")
            return redirect("control:store_detail", pk=pk)
        billing_svc.admin_mark_paid(sub, actor=request.user)
        messages.success(request, f"{store.name}: recorded as paid — subscription active.")
        return redirect("control:store_detail", pk=pk)


class StoreManagerAssignView(_StoreScope, View):
    def post(self, request, pk, *args, **kwargs):
        store = self.get_store(pk)
        form = StoreManagerAssignForm(request.POST)
        if form.is_valid():
            try:
                store_services.set_store_manager(
                    project=store, manager=form.cleaned_data.get("manager"),
                    actor=request.user, request=request,
                )
            except (ValidationError, PermissionDenied) as exc:
                messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
            else:
                messages.success(request, f"Manager updated for {store.name}.")
        else:
            messages.error(request, "Pick a valid DGC account.")
        return redirect("control:store_detail", pk=pk)


class StoreMemberAddView(_StoreScope, View):
    def post(self, request, pk, *args, **kwargs):
        store = self.get_store(pk)
        password = (request.POST.get("password") or "").strip()
        try:
            if password:
                validate_password(password)
            store_services.add_member(
                project=store, email=request.POST.get("email", ""),
                name=request.POST.get("name", ""), role=request.POST.get("role", ""),
                actor=request.user, request=request, password=password or None,
            )
            messages.success(request, "Team member added.")
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
        except Exception as exc:  # team_svc.TeamError etc.
            messages.error(request, str(exc))
        return redirect("control:store_detail", pk=pk)


class StoreArchiveView(_StoreScope, View):
    def post(self, request, pk, *args, **kwargs):
        store = self.get_store(pk)
        try:
            store_services.archive_store(project=store, actor=request.user, request=request)
        except PermissionDenied as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{store.name} archived — its storefront is now offline.")
        return redirect("control:store_detail", pk=pk)


class StoreUnarchiveView(_StoreScope, View):
    def post(self, request, pk, *args, **kwargs):
        store = self.get_store(pk)
        try:
            store_services.unarchive_store(project=store, actor=request.user, request=request)
        except PermissionDenied as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{store.name} reopened.")
        return redirect("control:store_detail", pk=pk)


class StoreDeleteView(_StoreScope, View):
    def post(self, request, pk, *args, **kwargs):
        store = self.get_store(pk)
        name = store.name
        try:
            store_services.delete_store(
                project=store, actor=request.user,
                confirm_name=request.POST.get("confirm_name", ""), request=request,
            )
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
            return redirect("control:store_detail", pk=pk)
        messages.success(request, f"{name} was permanently deleted.")
        return redirect("control:stores")


class StoreSwitchView(_StoreScope, View):
    def post(self, request, pk, *args, **kwargs):
        store = self.get_store(pk)
        request.session[ACTIVE_PROJECT_SESSION_KEY] = store.pk
        messages.info(request, f"Now working on {store.name}.")
        return redirect(request.POST.get("next") or "control:dashboard")
