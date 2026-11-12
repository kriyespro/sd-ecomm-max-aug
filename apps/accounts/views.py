from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import FormView, TemplateView

from apps.billing.models import Plan
from apps.core.services import safe_next

from . import google_oauth, ratelimit, team as team_svc, twofactor
from .signup import (
    confirm_self_signup, resend_self_signup_verification, self_signup,
    start_self_signup,
)

User = get_user_model()

_INPUT = ("mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm "
          "focus:border-slate-900 focus:outline-none")

# Where the pending Google profile lives between the callback and the
# "finish signup" form.
_PENDING = "signup_google"


def request_wants_password_form(request) -> bool:
    """?password=1 escape hatch — see LoginView.get_context_data."""
    return request.GET.get("password") == "1"


def _crm_lead_from_signup(project, *, name, phone, city, ref_code):
    """Every self-signup becomes a CRM lead so a DGC calls them fast. Strictly
    best-effort: a CRM problem must never break someone's signup."""
    try:
        from apps.crm import services as crm

        from .models import PlatformRole, Profile

        ref_user = None
        if (ref_code or "").strip():
            prof = Profile.objects.select_related("user").filter(
                affiliate_code=ref_code.strip()[:16], platform_role=PlatformRole.MANAGER,
                is_banned=False, user__is_active=True).first()
            ref_user = prof.user if prof else None
        crm.lead_from_signup(project, name=name, phone=phone, city=city, ref_user=ref_user)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception("crm: could not create a lead from signup %s", project.pk)


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.jinja"
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["google_enabled"] = google_oauth.is_enabled()
        # Google-only login for now -- the password form is hidden by
        # default so it isn't offered/discoverable, but stays reachable at
        # ?password=1 as a deliberate escape hatch: team accounts are
        # provisioned with a one-time password (apps.accounts.team
        # .provision_member), not a Google-linked identity, so hard-removing
        # password auth entirely risks locking them (and any non-Google
        # superadmin) out with no way back in short of server/DB access.
        ctx["show_password_form"] = request_wants_password_form(self.request)
        return ctx

    def post(self, request, *args, **kwargs):
        username = request.POST.get("username", "")
        if ratelimit.is_locked(request, username):
            form = self.get_form()
            form.add_error(None, ratelimit.LOCK_MESSAGE)
            return self.render_to_response(self.get_context_data(form=form))
        if google_oauth.is_enabled() and not request_wants_password_form(request):
            form = self.get_form()
            form.add_error(None, "Password sign-in is off for now — use Continue with Google.")
            return self.render_to_response(self.get_context_data(form=form))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        ratelimit.clear(self.request, user.get_username())
        if user.is_staff and twofactor.is_enabled(user):
            # Password check passed, but any Mission Control account
            # (platform admin, store owner/manager/staff, DGC) with 2FA
            # enabled isn't logged in yet — stash the pending user + intended
            # destination and require the TOTP/backup code first.
            self.request.session["2fa_user_id"] = user.pk
            self.request.session["2fa_next"] = self.get_success_url()
            return redirect("accounts:2fa_verify")
        return super().form_valid(form)

    def form_invalid(self, form):
        ratelimit.record_failure(self.request, self.request.POST.get("username", ""))
        return super().form_invalid(form)


class LogoutView(auth_views.LogoutView):
    pass


class TwoFactorCodeForm(forms.Form):
    code = forms.CharField(
        label="Code", max_length=32,
        widget=forms.TextInput(attrs={
            "autocomplete": "one-time-code", "inputmode": "numeric",
            "autofocus": True, "class": _INPUT,
        }),
    )


class TwoFactorSetupView(LoginRequiredMixin, FormView):
    """Mandatory for every Mission Control account (is_staff) — TwoFactorEnforcementMiddleware
    routes them here on every request until it's confirmed. The secret is
    held in the session (never the DB) until the admin proves they can
    generate a real code with it; only then is it persisted + enabled."""

    template_name = "accounts/2fa_setup.jinja"
    form_class = TwoFactorCodeForm

    def get(self, request, *args, **kwargs):
        profile = request.user.profile
        if profile.totp_enabled:
            return self.render_to_response(self.get_context_data(already_enabled=True))
        secret = request.session.get("2fa_setup_secret") or twofactor.generate_secret()
        request.session["2fa_setup_secret"] = secret
        uri = twofactor.provisioning_uri(request.user, secret)
        return self.render_to_response(self.get_context_data(
            form=self.get_form(), secret=secret, uri=uri,
            qr_data_uri=twofactor.qr_data_uri(uri),
        ))

    def post(self, request, *args, **kwargs):
        profile = request.user.profile
        if profile.totp_enabled:
            return redirect("control:dashboard")
        secret = request.session.get("2fa_setup_secret")
        form = self.get_form()
        if not (secret and form.is_valid()):
            form.add_error(None, "Session expired — reload the page and scan the code again.")
            uri = twofactor.provisioning_uri(request.user, secret) if secret else ""
            return self.render_to_response(self.get_context_data(
                form=form, secret=secret, uri=uri,
                qr_data_uri=twofactor.qr_data_uri(uri) if uri else "",
            ))
        codes = twofactor.enable(profile, secret=secret, code=form.cleaned_data["code"])
        if codes is None:
            form.add_error("code", "Incorrect code — check your authenticator app and try again.")
            uri = twofactor.provisioning_uri(request.user, secret)
            return self.render_to_response(self.get_context_data(
                form=form, secret=secret, uri=uri, qr_data_uri=twofactor.qr_data_uri(uri),
            ))
        del request.session["2fa_setup_secret"]
        return self.render_to_response(self.get_context_data(enabled=True, backup_codes=codes))


class TwoFactorVerifyView(FormView):
    """Second step of login for any Mission Control account with 2FA
    enabled. Reached only via LoginView.form_valid stashing a pending user
    id in the session — never reachable with a valid session of its own."""

    template_name = "accounts/2fa_verify.jinja"
    form_class = TwoFactorCodeForm

    def dispatch(self, request, *args, **kwargs):
        if not request.session.get("2fa_user_id"):
            return redirect("accounts:login")
        return super().dispatch(request, *args, **kwargs)

    def _pending_user(self):
        return User.objects.filter(
            pk=self.request.session.get("2fa_user_id")
        ).select_related("profile").first()

    def form_valid(self, form):
        user = self._pending_user()
        if user is None:
            return redirect("accounts:login")
        ident = f"2fa:{user.get_username()}"
        if ratelimit.is_locked(self.request, ident):
            form.add_error(None, ratelimit.LOCK_MESSAGE)
            return self.form_invalid(form)
        profile = user.profile
        code = form.cleaned_data["code"]
        ok = twofactor.verify_totp(profile.totp_secret, code) or twofactor.consume_backup_code(profile, code)
        if not ok:
            ratelimit.record_failure(self.request, ident)
            form.add_error("code", "Incorrect code.")
            return self.form_invalid(form)
        ratelimit.clear(self.request, ident)
        del self.request.session["2fa_user_id"]
        next_url = self.request.session.pop("2fa_next", None) or reverse("control:dashboard")
        login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
        return redirect(safe_next(self.request, next_url, reverse("control:dashboard")))


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
        # Ad attribution (utm_*/gclid/fbclid + the landing page slug), carried
        # on the CTA link from /<landing-slug>/. Stored on the session so it
        # survives the Google OAuth round trip; saved on the new store later.
        from apps.core.landing_pages import PAGES, attribution_from

        source = attribution_from(request.GET)
        lp = (request.GET.get("lp") or "").strip()
        if lp in PAGES:
            source["lp"] = lp
        if source:
            request.session["signup_source"] = source
        # Store name typed into the landing-page hero form; pre-fills the
        # "name your store" step after Google sign-in. Plain text, length-capped.
        store = " ".join((request.GET.get("store") or "").split())[:120]
        if store:
            request.session["signup_store_name"] = store
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_signup_page_context(self.request))
        ctx.setdefault("password_form", SignupPasswordForm())
        return ctx


def _valid_plan_code(request):
    code = request.GET.get("plan") or ""
    return Plan.objects.filter(
        is_active=True, is_public=True, code=code
    ).values_list("code", flat=True).first() or ""


def _signup_page_context(request):
    from apps.billing.models import BillingSettings

    return {
        "google_enabled": google_oauth.is_enabled(),
        "trial_days": BillingSettings.load().self_signup_trial_days,
        "plan": _valid_plan_code(request),
    }


class SignupPasswordForm(forms.Form):
    """The password-signup alternative to Google — one shot, same page as
    ``SignupView``. Unlike Google, there's no other proof this address is
    real, so submitting this form never creates anything: see
    ``start_self_signup``/``SignupVerifyView``."""

    name = forms.CharField(
        label="Your name", max_length=140,
        widget=forms.TextInput(attrs={"class": _INPUT, "placeholder": "Jane Doe"}),
    )
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"class": _INPUT, "autocomplete": "email"}),
    )
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
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"class": _INPUT, "autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(attrs={"class": _INPUT, "autocomplete": "new-password"}),
    )
    city = forms.CharField(label="City", max_length=80, required=False,
                           widget=forms.TextInput(attrs={"class": _INPUT}))
    state = forms.CharField(label="State", max_length=80, required=False,
                            widget=forms.TextInput(attrs={"class": _INPUT}))
    postal_code = forms.CharField(label="Pincode", max_length=12, required=False,
                                  widget=forms.TextInput(attrs={"class": _INPUT}))

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords don't match.")
        if p1:
            try:
                validate_password(p1)
            except ValidationError as exc:
                for msg in exc.messages:
                    self.add_error("password", msg)
        return cleaned


class PasswordSignupView(View):
    """Posts from the same ``signup.jinja`` page as the Google button."""

    def post(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("control:dashboard")
        form = SignupPasswordForm(request.POST)
        email = (request.POST.get("email") or "").strip().lower()
        if ratelimit.verify_signup_is_locked(request, email):
            form.add_error(None, ratelimit.VERIFY_SIGNUP_LOCK_MESSAGE)
            return render(request, SignupView.template_name, {
                **_signup_page_context(request), "password_form": form,
            })
        ratelimit.record_verify_signup_attempt(request, email)
        if not form.is_valid():
            return render(request, SignupView.template_name, {
                **_signup_page_context(request), "password_form": form,
            })

        plan = Plan.objects.filter(
            is_active=True, is_public=True, code=request.GET.get("plan") or ""
        ).first()
        try:
            start_self_signup(
                name=form.cleaned_data["name"], email=form.cleaned_data["email"],
                store_name=form.cleaned_data["store_name"], phone=form.cleaned_data["phone"],
                password=form.cleaned_data["password"], plan=plan,
                ref_code=request.session.get("signup_ref", ""),
                city=form.cleaned_data["city"], state=form.cleaned_data["state"],
                postal_code=form.cleaned_data["postal_code"],
                signup_source=request.session.get("signup_source"),
                capi_context={
                    "ip": request.META.get("REMOTE_ADDR", ""),
                    "ua": request.META.get("HTTP_USER_AGENT", ""),
                    "fbp": request.COOKIES.get("_fbp", ""),
                    "fbc": request.COOKIES.get("_fbc", ""),
                },
                request=request,
            )
        except ValidationError as exc:
            for msg in exc.messages:
                form.add_error(None, msg)
            return render(request, SignupView.template_name, {
                **_signup_page_context(request), "password_form": form,
            })
        return redirect(f"{reverse('accounts:signup_check_email')}?email={form.cleaned_data['email']}")


class SignupCheckEmailView(TemplateView):
    template_name = "accounts/signup_check_email.jinja"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["email"] = self.request.GET.get("email", "")
        return ctx


class SignupResendView(View):
    def post(self, request, *args, **kwargs):
        email = (request.POST.get("email") or "").strip().lower()
        if not ratelimit.verify_signup_is_locked(request, email):
            ratelimit.record_verify_signup_attempt(request, email)
            resend_self_signup_verification(email=email, request=request)
        messages.success(
            request, "If that address has a pending signup, we've resent the confirmation link."
        )
        return redirect(f"{reverse('accounts:signup_check_email')}?email={email}")


class SignupVerifyView(View):
    """Confirms a password-based self-signup. POST (not a bare GET) actually
    creates the account/store — a corporate mail-scanner prefetching the
    link must never silently consume it."""

    template_name = "accounts/signup_verify.jinja"

    def _pending(self, token):
        from .models import PendingVerification

        return PendingVerification.objects.filter(
            kind=PendingVerification.Kind.SELF_SIGNUP, token=token,
        ).first()

    def get(self, request, token, *args, **kwargs):
        pending = self._pending(token)
        return render(request, self.template_name, {
            "pending": pending,
            "invalid": pending is None or pending.is_used or pending.is_expired,
        })

    def post(self, request, token, *args, **kwargs):
        pending = self._pending(token)
        if pending is None:
            return render(request, self.template_name, {"pending": None, "invalid": True})
        try:
            project, user, _created = confirm_self_signup(pending=pending, request=request)
        except ValidationError as exc:
            return render(request, self.template_name, {
                "pending": pending, "invalid": True, "error": " ".join(exc.messages),
            })

        payload = pending.payload
        ref_code = payload.get("ref_code", "")
        source = payload.get("signup_source") or {}
        if source:
            project.signup_source = source
            project.save(update_fields=["signup_source"])
        _track_signup(
            request, project=project, email=pending.email, plan=project.subscription.plan,
            phone=payload.get("phone", ""), name=payload.get("name", ""),
            city=payload.get("city", ""), state=payload.get("state", ""),
            postal_code=payload.get("postal_code", ""),
        )
        _crm_lead_from_signup(
            project, name=payload.get("name", ""), phone=payload.get("phone", ""),
            city=payload.get("city", ""), ref_code=ref_code,
        )
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        twofactor.grant_signup_grace(request)
        return redirect("control:onboarding")


class TeamInviteAcceptView(View):
    """Public, no login required — clicking the emailed link is itself the
    proof of identity (same reasoning as GoogleCallbackView's "known email ->
    log in" branch). Only here does ``team.provision_member``/``add_member``
    actually take effect."""

    template_name = "accounts/invite_accept.jinja"

    def _ctx(self, invite, error=""):
        needs_password = bool(
            invite and not User.objects.filter(email__iexact=invite.email).exists()
        )
        return {
            "invite": invite,
            "invalid": invite is None or invite.is_used or invite.is_expired,
            "needs_password": needs_password,
            "error": error,
        }

    def get(self, request, token, *args, **kwargs):
        invite = team_svc.get_invite(token)
        return render(request, self.template_name, self._ctx(invite))

    def post(self, request, token, *args, **kwargs):
        invite = team_svc.get_invite(token)
        if invite is None:
            return render(request, self.template_name, self._ctx(invite))
        try:
            membership, user = team_svc.confirm_invite(
                invite=invite, password=request.POST.get("password") or None, request=request,
            )
        except team_svc.TeamError as exc:
            return render(request, self.template_name, self._ctx(invite, error=str(exc)))
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(
            request,
            f"You're now on the {membership.project.name} team as {membership.get_role_display()}.",
        )
        return redirect("control:dashboard")


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
            if created:
                twofactor.grant_signup_grace(request)
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
            return redirect(safe_next(request, flow.get("next"), settings.LOGIN_REDIRECT_URL))

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

    def get_initial(self):
        initial = super().get_initial()
        store = self.request.session.get("signup_store_name")
        if store:
            initial["store_name"] = store
        return initial

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

        ref_code = self.request.session.get("signup_ref", "")
        self.request.session.pop(_PENDING, None)
        self.request.session.pop("signup_ref", None)
        self.request.session.pop("signup_store_name", None)
        source = self.request.session.pop("signup_source", None)
        if source:
            _project.signup_source = source
            _project.save(update_fields=["signup_source"])
        _track_signup(self.request, project=_project, email=self.pending["email"],
                     plan=plan, phone=form.cleaned_data["phone"],
                     name=self.pending.get("name") or "",
                     city=form.cleaned_data["city"], state=form.cleaned_data["state"],
                     postal_code=form.cleaned_data["postal_code"])
        _crm_lead_from_signup(_project, name=self.pending.get("name") or "",
                              phone=form.cleaned_data["phone"], city=form.cleaned_data["city"],
                              ref_code=ref_code)
        login(self.request, user)
        twofactor.grant_signup_grace(self.request)
        return redirect("control:onboarding")
