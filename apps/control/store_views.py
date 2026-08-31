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
from apps.projects.models import Project
from apps.projects.services import projects_for_user

from . import store_services
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
    primary_domain = forms.CharField(required=False, label="Primary domain",
                                     help_text="Optional. e.g. shop.brand.com")
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
    period = forms.ChoiceField(choices=BillingPeriod.choices, initial=BillingPeriod.MONTHLY)
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

    def clean_owner_password(self):
        pw = self.cleaned_data.get("owner_password") or ""
        if pw:
            validate_password(pw)
        return pw


class StoreListView(_StoreScope, ListView):
    template_name = "control/stores/list.jinja"
    context_object_name = "stores"

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

    def form_valid(self, form):
        actor = self.request.user
        manager = form.cleaned_data.get("manager")
        if not is_platform_admin(actor):
            # Manager signs the store up under themselves.
            manager = actor if actor.profile.platform_role == PlatformRole.MANAGER else None
        try:
            project, owner, created = store_services.create_store(
                name=form.cleaned_data["name"],
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
        return ctx


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


class StoreSwitchView(_StoreScope, View):
    def post(self, request, pk, *args, **kwargs):
        store = self.get_store(pk)
        request.session[ACTIVE_PROJECT_SESSION_KEY] = store.pk
        messages.info(request, f"Now working on {store.name}.")
        return redirect(request.POST.get("next") or "control:dashboard")
