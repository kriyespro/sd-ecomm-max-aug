"""First-run setup wizard for a new store owner.

Two things, one screen: contact details (saved to the storefront profile) and
the store vertical (fashion / jewellery / clothing / FMCG). Finishing it sets
``feature_flags["onboarded"]`` so ``ActiveProjectMixin`` stops redirecting here.
"""

from django import forms
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import FormView, View

from apps.accounts.permissions import OWNER_MANAGER, StoreRoleRequiredMixin
from apps.cms.models import StoreProfile
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.projects.verticals import VERTICALS, vertical_of

from .mixins import ActiveProjectMixin

TEXT = ("mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 "
        "text-sm text-slate-900 shadow-sm focus:border-slate-500 focus:outline-none "
        "focus:ring-1 focus:ring-slate-500")


class OnboardingForm(forms.Form):
    contact_email = forms.EmailField(
        label="Contact email",
        widget=forms.EmailInput(attrs={"class": TEXT, "placeholder": "you@brand.com"}),
        help_text="Shown to customers in your storefront footer and on order emails.",
    )
    contact_phone = forms.CharField(
        label="Phone / WhatsApp", max_length=32, required=False,
        widget=forms.TextInput(attrs={"class": TEXT, "placeholder": "+91 98xxxxxxxx"}),
    )
    address = forms.CharField(
        label="Business address", required=False,
        widget=forms.Textarea(attrs={"class": TEXT, "rows": 3,
                                     "placeholder": "Street, city, PIN"}),
    )
    vertical = forms.ChoiceField(
        label="What do you sell?", choices=VERTICALS, widget=forms.RadioSelect,
    )


class OnboardingView(StoreRoleRequiredMixin, ActiveProjectMixin, FormView):
    template_name = "control/onboarding.jinja"
    form_class = OnboardingForm
    required_store_roles = OWNER_MANAGER

    def get_initial(self):
        p = self.active_project
        prof = StoreProfile.objects.filter(project=p).first()
        return {
            "contact_email": (prof and prof.support_email) or "",
            "contact_phone": (prof and (prof.support_phone or prof.whatsapp)) or "",
            "address": (prof and prof.address) or "",
            "vertical": vertical_of(p) or None,
        }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["store"] = self.active_project
        return ctx

    def form_valid(self, form):
        p = self.active_project
        cd = form.cleaned_data

        profile, _ = StoreProfile.objects.get_or_create(project=p)
        profile.support_email = cd["contact_email"]
        if cd.get("contact_phone"):
            profile.support_phone = cd["contact_phone"]
            if not profile.whatsapp:
                profile.whatsapp = cd["contact_phone"]
        if cd.get("address"):
            profile.address = cd["address"]
        profile.save()

        flags = p.feature_flags or {}
        flags["vertical"] = cd["vertical"]
        flags["onboarded"] = True
        p.feature_flags = flags
        p.save(update_fields=["feature_flags"])

        record_audit(
            actor=self.request.user, project=p, action=AuditLog.Action.UPDATE,
            target=p, changes={"onboarding": "completed", "vertical": cd["vertical"]},
            request=self.request,
        )
        messages.success(
            self.request,
            "You're all set. Your store already has demo products — edit them, "
            "or add your own.",
        )
        return redirect("control:product_list")


class OnboardingSkipView(StoreRoleRequiredMixin, ActiveProjectMixin, View):
    """Escape hatch — mark onboarded without filling anything in."""

    required_store_roles = OWNER_MANAGER
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        p = self.active_project
        flags = p.feature_flags or {}
        flags["onboarded"] = True
        flags.setdefault("vertical", "")
        p.feature_flags = flags
        p.save(update_fields=["feature_flags"])
        messages.info(request, "Setup skipped. Finish it anytime from Store profile.")
        return redirect(request.POST.get("next") or reverse("control:product_list"))
