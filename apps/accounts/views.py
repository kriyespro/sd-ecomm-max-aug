from django import forms
from django.contrib.auth import get_user_model, login
from django.contrib.auth import views as auth_views
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.views.generic import FormView

from apps.billing.models import Plan

from .signup import self_signup

User = get_user_model()

_INPUT = ("mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm "
          "focus:border-slate-900 focus:outline-none")


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.jinja"
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    pass


class SignupForm(forms.Form):
    full_name = forms.CharField(
        label="Your name", max_length=120, required=False,
        widget=forms.TextInput(attrs={"class": _INPUT, "autocomplete": "name"}),
    )
    store_name = forms.CharField(
        label="Store name", max_length=120,
        widget=forms.TextInput(attrs={"class": _INPUT, "placeholder": "Bright & Co."}),
    )
    email = forms.EmailField(
        label="Email", widget=forms.EmailInput(
            attrs={"class": _INPUT, "autocomplete": "email"}
        ),
    )
    password = forms.CharField(
        label="Password", strip=False,
        widget=forms.PasswordInput(attrs={"class": _INPUT, "autocomplete": "new-password"}),
    )
    plan = forms.ModelChoiceField(
        queryset=Plan.objects.filter(is_active=True, is_public=True),
        required=False, widget=forms.HiddenInput, to_field_name="code",
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        existing = User.objects.filter(email__iexact=email).first()
        if existing and existing.has_usable_password():
            raise ValidationError("An account with this email already exists — sign in instead.")
        return email

    def clean_password(self):
        pw = self.cleaned_data["password"]
        validate_password(pw)
        return pw


class SignupView(FormView):
    template_name = "accounts/signup.jinja"
    form_class = SignupForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("control:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        code = self.request.GET.get("plan") or ""
        valid = Plan.objects.filter(
            is_active=True, is_public=True, code=code
        ).values_list("code", flat=True).first()
        return {"plan": valid}

    def get_context_data(self, **kwargs):
        from apps.billing.models import BillingSettings

        ctx = super().get_context_data(**kwargs)
        ctx["plans"] = Plan.objects.filter(is_active=True, is_public=True).order_by("sort_order")
        ctx["trial_days"] = BillingSettings.load().self_signup_trial_days
        return ctx

    def form_valid(self, form):
        cd = form.cleaned_data
        try:
            _project, user, _ = self_signup(
                name=cd["full_name"], email=cd["email"], password=cd["password"],
                store_name=cd["store_name"], plan=cd.get("plan"), request=self.request,
            )
        except ValidationError as exc:
            for msg in exc.messages:
                form.add_error(None, msg)
            return self.form_invalid(form)

        login(self.request, user)
        return redirect("control:onboarding")
