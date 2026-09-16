from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth import views as auth_views
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import FormView, TemplateView

from apps.billing.models import Plan

from . import google_oauth, ratelimit
from .signup import self_signup

User = get_user_model()

_INPUT = ("mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm "
          "focus:border-slate-900 focus:outline-none")


def _safe_next(request, next_url):
    """``next`` round-trips through the Google OAuth session flow — still
    attacker-supplied (it started as ``?next=`` on the sign-in link), so it
    must be revalidated right before use, same as apps.control.store_views."""
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return settings.LOGIN_REDIRECT_URL

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

    def post(self, request, *args, **kwargs):
        username = request.POST.get("username", "")
        if ratelimit.is_locked(request, username):
            form = self.get_form()
            form.add_error(None, ratelimit.LOCK_MESSAGE)
            return self.render_to_response(self.get_context_data(form=form))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        ratelimit.clear(self.request, form.get_user().get_username())
        return super().form_valid(form)

    def form_invalid(self, form):
        ratelimit.record_failure(self.request, self.request.POST.get("username", ""))
        return super().form_invalid(form)


class LogoutView(auth_views.LogoutView):
    pass


# --- Public self-signup: Google only --------------------------------

class SignupView(TemplateView):
    template_name = "accounts/signup.jinja"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("control:dashboard")
        # A DGC affiliate link (?ref=<code>) — stashed in the session so it
        # survives the Google OAuth round trip and is still there when
        # SignupCompleteView finally creates the store. An absent or later
        # invalid code is resolved (and silently dropped) inside self_signup,
        # never here — a bad code must never block signup.
        ref = (request.GET.get("ref") or "").strip()[:16]
        if ref:
            request.session["signup_ref"] = ref
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


class AffiliateJoinView(TemplateView):
    """Public, open affiliate signup — anyone becomes a DGC instantly, no
    application or admin review (that curated path is /partners/). Already
    logged in? Join on the spot, no OAuth round trip needed — we already
    know who they are."""

    template_name = "accounts/affiliate_join.jinja"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.email:
            from .affiliate_signup import join_as_affiliate

            try:
                _user, _created, upgraded = join_as_affiliate(
                    name=request.user.get_full_name() or request.user.username,
                    email=request.user.email, request=request,
                )
            except ValidationError as exc:
                for msg in exc.messages:
                    messages.error(request, msg)
                return redirect("control:dashboard")
            if upgraded:
                messages.success(request, "You're in — here's your referral link.")
            else:
                messages.info(request, "You already have affiliate access.")
            return redirect("control:my_commissions")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["google_enabled"] = google_oauth.is_enabled()
        return ctx


class GoogleStartView(View):
    def get(self, request, *args, **kwargs):
        kind = request.GET.get("kind") or "store"
        if not google_oauth.is_enabled():
            messages.error(request, "Google sign-in isn't configured yet.")
            return redirect("accounts:affiliate_join" if kind == "affiliate" else "accounts:signup")
        plan = request.GET.get("plan") or ""
        next_url = request.GET.get("next") or ""
        return redirect(google_oauth.start(request, plan=plan, next_url=next_url, kind=kind))


class GoogleCallbackView(View):
    def get(self, request, *args, **kwargs):
        flow = request.session.pop(google_oauth.SESSION_KEY, None) or {}
        kind = flow.get("kind") or "store"
        fallback = "accounts:affiliate_join" if kind == "affiliate" else "accounts:signup"

        if request.GET.get("error"):
            messages.error(request, "Google sign-in was cancelled.")
            return redirect(fallback)
        if not flow or not request.GET.get("state") or request.GET["state"] != flow.get("state"):
            messages.error(request, "Sign-in session expired — please try again.")
            return redirect(fallback)
        code = request.GET.get("code")
        if not code:
            return redirect(fallback)

        try:
            info = google_oauth.exchange_code(request, code)
        except google_oauth.OAuthError as exc:
            messages.error(request, str(exc))
            return redirect(fallback)

        if kind == "affiliate":
            from .affiliate_signup import join_as_affiliate

            try:
                user, created, upgraded = join_as_affiliate(
                    name=info["name"], email=info["email"], request=request,
                )
            except ValidationError as exc:
                for msg in exc.messages:
                    messages.error(request, msg)
                return redirect(fallback)
            login(request, user)
            if upgraded:
                messages.success(request, "You're in — here's your referral link.")
            else:
                messages.info(request, "You already have affiliate access.")
            return redirect("control:my_commissions")

        existing = User.objects.filter(email__iexact=info["email"]).first()
        if existing and (existing.has_usable_password() or existing.memberships.exists()
                         or existing.is_superuser):
            # Known account — treat this as a sign-in.
            login(request, existing)
            return redirect(_safe_next(request, flow.get("next")))

        request.session[_PENDING] = {
            "email": info["email"], "name": info["name"], "plan": flow.get("plan") or "",
        }
        return redirect("accounts:signup_complete")


def _track_signup(request, *, project, email, plan, phone="", name="",
                  city="", state="", postal_code=""):
    """Fire the platform's own Meta CAPI "CompleteRegistration" event for a new
    trial store. No browser pixel covers this event (it's platform-level, not
    per-store), so this server call is Meta's only signal for it — match
    quality (ip/ua/fbp/fbc, name, address, country — not just a hashed email)
    directly affects whether Meta can attribute the conversion to an ad
    click. Best-effort — a broker hiccup must never break signup.

    gender/date-of-birth are Meta's other two recommended match fields but
    aren't collected anywhere in this signup flow — a store owner signing up
    has no reason to give them, unlike name/phone/address which double as
    real account and store data."""
    try:
        from apps.marketing.capi import hash_user_data
        from apps.marketing.models import PlatformTrackingSettings
        from apps.marketing.tasks import send_platform_capi_event

        if not PlatformTrackingSettings.load().capi_ready:
            return
        first_name, _, last_name = (name or "").strip().partition(" ")
        send_platform_capi_event.delay(
            event_name="CompleteRegistration",
            event_id=f"signup-{project.pk}",
            user_data=hash_user_data(
                email=email, phone=phone, external_id=str(project.pk),
                first_name=first_name, last_name=last_name, country=project.country,
                city=city, state=state, zip_code=postal_code,
                client_ip=request.META.get("REMOTE_ADDR", ""),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                fbp=request.COOKIES.get("_fbp", ""),
                fbc=request.COOKIES.get("_fbc", ""),
            ),
            custom_data={"content_name": plan.name if plan else "trial"},
            event_source_url=request.build_absolute_uri("/accounts/signup/"),
        )
    except Exception:  # noqa: BLE001
        pass


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
    city = forms.CharField(
        label="City", max_length=80,
        widget=forms.TextInput(attrs={"class": _INPUT, "placeholder": "Mumbai", "autocomplete": "address-level2"}),
    )
    state = forms.CharField(
        label="State", max_length=80,
        widget=forms.TextInput(attrs={"class": _INPUT, "placeholder": "Maharashtra", "autocomplete": "address-level1"}),
    )
    postal_code = forms.CharField(
        label="Pincode", max_length=12,
        widget=forms.TextInput(attrs={
            "class": _INPUT, "placeholder": "400001",
            "autocomplete": "postal-code", "inputmode": "numeric",
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
                ref_code=self.request.session.get("signup_ref", ""),
                city=form.cleaned_data["city"],
                state=form.cleaned_data["state"],
                postal_code=form.cleaned_data["postal_code"],
            )
        except ValidationError as exc:
            for msg in exc.messages:
                form.add_error(None, msg)
            return self.form_invalid(form)

        self.request.session.pop(_PENDING, None)
        self.request.session.pop("signup_ref", None)
        _track_signup(self.request, project=_project, email=self.pending["email"],
                     plan=plan, phone=form.cleaned_data["phone"],
                     name=self.pending.get("name") or "",
                     city=form.cleaned_data["city"], state=form.cleaned_data["state"],
                     postal_code=form.cleaned_data["postal_code"])
        login(self.request, user)
        return redirect("control:onboarding")
