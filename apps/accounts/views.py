from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth import views as auth_views
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.views import View
from django.views.generic import FormView, TemplateView

from apps.billing.models import Plan

from . import google_oauth
from .signup import self_signup

User = get_user_model()

_INPUT = ("mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm "
          "focus:border-slate-900 focus:outline-none")

# Where the pending Google profile lives between the callback and the
# "finish signup" form.
_PENDING = "signup_google"


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.jinja"
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["google_enabled"] = google_oauth.is_enabled()
        return ctx


class LogoutView(auth_views.LogoutView):
    pass


# --- Public self-signup: Google only --------------------------------

class SignupView(TemplateView):
    template_name = "accounts/signup.jinja"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("control:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from apps.billing.models import BillingSettings

        ctx = super().get_context_data(**kwargs)
        ctx["google_enabled"] = google_oauth.is_enabled()
        ctx["trial_days"] = BillingSettings.load().self_signup_trial_days
        ctx["plan"] = self._valid_plan()
        return ctx

    def _valid_plan(self):
        code = self.request.GET.get("plan") or ""
        return Plan.objects.filter(
            is_active=True, is_public=True, code=code
        ).values_list("code", flat=True).first() or ""


class GoogleStartView(View):
    def get(self, request, *args, **kwargs):
        if not google_oauth.is_enabled():
            messages.error(request, "Google sign-in isn't configured yet.")
            return redirect("accounts:signup")
        plan = request.GET.get("plan") or ""
        next_url = request.GET.get("next") or ""
        return redirect(google_oauth.start(request, plan=plan, next_url=next_url))


class GoogleCallbackView(View):
    def get(self, request, *args, **kwargs):
        flow = request.session.pop(google_oauth.SESSION_KEY, None) or {}
        if request.GET.get("error"):
            messages.error(request, "Google sign-in was cancelled.")
            return redirect("accounts:signup")
        if not flow or not request.GET.get("state") or request.GET["state"] != flow.get("state"):
            messages.error(request, "Sign-in session expired — please try again.")
            return redirect("accounts:signup")
        code = request.GET.get("code")
        if not code:
            return redirect("accounts:signup")

        try:
            info = google_oauth.exchange_code(request, code)
        except google_oauth.OAuthError as exc:
            messages.error(request, str(exc))
            return redirect("accounts:signup")

        existing = User.objects.filter(email__iexact=info["email"]).first()
        if existing and (existing.has_usable_password() or existing.memberships.exists()
                         or existing.is_superuser):
            # Known account — treat this as a sign-in.
            login(request, existing)
            return redirect(flow.get("next") or settings.LOGIN_REDIRECT_URL)

        request.session[_PENDING] = {
            "email": info["email"], "name": info["name"], "plan": flow.get("plan") or "",
        }
        return redirect("accounts:signup_complete")


class SignupCompleteForm(forms.Form):
    store_name = forms.CharField(
        label="Store name", max_length=120,
        widget=forms.TextInput(attrs={"class": _INPUT, "placeholder": "Bright & Co."}),
    )
    phone = forms.CharField(
        label="Mobile number", max_length=20,
        widget=forms.TextInput(attrs={
            "class": _INPUT, "placeholder": "+91 98xxxxxxxx",
            "autocomplete": "tel", "inputmode": "tel",
        }),
    )


class SignupCompleteView(FormView):
    template_name = "accounts/signup_complete.jinja"
    form_class = SignupCompleteForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("control:dashboard")
        self.pending = request.session.get(_PENDING)
        if not self.pending:
            messages.error(request, "Start by continuing with Google.")
            return redirect("accounts:signup")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["email"] = self.pending["email"]
        return ctx

    def form_valid(self, form):
        plan_code = self.pending.get("plan") or ""
        plan = Plan.objects.filter(
            is_active=True, is_public=True, code=plan_code
        ).first()
        try:
            _project, user, _ = self_signup(
                name=self.pending.get("name") or "",
                email=self.pending["email"],
                store_name=form.cleaned_data["store_name"],
                phone=form.cleaned_data["phone"],
                plan=plan, oauth=True, request=self.request,
            )
        except ValidationError as exc:
            for msg in exc.messages:
                form.add_error(None, msg)
            return self.form_invalid(form)

        self.request.session.pop(_PENDING, None)
        login(self.request, user)
        return redirect("control:onboarding")
