"""Public self-serve signup: one form -> a user, a store, a short free trial,
then the setup wizard.

Partner- and platform-provisioned stores go through ``apps.control.store_services``
instead and get the longer ``BillingSettings.trial_days`` trial.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import (
    Membership, PendingVerification, PlatformRole, Profile, StoreRole, UiMode,
)
from apps.billing import services as billing_svc
from apps.billing.models import BillingSettings, Plan
from apps.core.models import AuditLog
from apps.core.services import absolute_url, record_audit
from apps.projects.models import Project

User = get_user_model()

SIGNUP_VERIFICATION_WINDOW = timezone.timedelta(hours=48)


@transaction.atomic
def self_signup(*, name, email, store_name, phone, password=None, password_hash=None,
                plan=None, oauth=False, request=None, ref_code="", city="", state="", postal_code=""):
    """Create the account + store. Returns ``(project, user, user_was_created)``.

    Public signup is Google-only, so ``oauth=True`` (no password) is the norm;
    ``password`` is still accepted for tests / a future email path. ``phone`` is
    mandatory. Raises ``ValidationError`` on bad input or a taken account.

    ``ref_code`` is a DGC's ``Profile.affiliate_code`` lifted from the signup
    link's ``?ref=``. An unknown/blank code is silently ignored — a bad or
    stale code must never block signup, it just earns nobody a commission.
    It only ever sets ``Subscription.referred_by`` (commission), never
    ``manager`` (store access) — an affiliate link must never hand the
    referring DGC access to a store they didn't set up.

    ``city``/``state``/``postal_code`` land on the store's own StoreProfile
    (its address, shown in the storefront footer) — collected at signup
    mainly so the platform's CompleteRegistration CAPI event can include
    Meta's ct/st/zp match-quality fields, but genuinely useful as the store's
    address regardless of that.
    """
    email = (email or "").strip().lower()
    store_name = (store_name or "").strip()
    phone = (phone or "").strip()
    if not email:
        raise ValidationError("Email is required.")
    if not store_name:
        raise ValidationError("Store name is required.")
    if not phone:
        raise ValidationError("Mobile number is required.")
    if not oauth and not password and not password_hash:
        raise ValidationError("A password is required.")

    existing = User.objects.filter(email__iexact=email).first()
    if existing and existing.has_usable_password():
        raise ValidationError(
            "An account with this email already exists — sign in instead."
        )

    user = existing or User(username=email[:150], email=email)
    first, _, last = (name or "").strip().partition(" ")
    user.first_name, user.last_name = first, last
    user.is_staff = True
    user.is_active = True
    if password_hash:
        user.password = password_hash
    elif oauth or not password:
        user.set_unusable_password()
    else:
        user.set_password(password)
    user.save()

    # ``ensure_profile`` (post_save signal) has already made the Profile and
    # cached it on ``user`` — mutate that instance so ``user.profile`` stays fresh.
    profile = getattr(user, "profile", None) or Profile.objects.get_or_create(user=user)[0]
    profile.phone = phone
    # A brand-new self-serve owner starts in the trimmed "Easy" Mission
    # Control nav — see apps.control.navigation._EASY_MODE_ITEMS. Everyone
    # else (team members, partner/DGC-provisioned stores, test fixtures)
    # keeps the Profile model default (Expert) — this is the one deliberate
    # opt-in, not a blanket default.
    profile.ui_mode = UiMode.EASY
    profile.save(update_fields=["phone", "ui_mode"])

    from apps.core.brand import brand_for_request

    brand_key = brand_for_request(request).key if request is not None else ""
    project = Project.objects.create(
        name=store_name, status=Project.Status.ACTIVE, currency="INR", country="IN",
        brand="" if brand_key == "default" else brand_key,
    )
    Membership.objects.update_or_create(
        user=user, project=project,
        defaults={"role": StoreRole.OWNER, "is_active": True},
    )

    # Auto platform subdomain so the storefront is live right away. Editable in
    # the setup wizard. Never block signup on it.
    try:
        from apps.projects import subdomains

        if subdomains.base_domain(project):
            slug = subdomains.unique_slug(store_name or email.split("@")[0], project=project)
            subdomains.assign(project, slug)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception("subdomain assignment failed")

    # The billing post_save signal should already have opened a
    # standard-length trial with no manager — but it swallows its own errors
    # (e.g. no active/public plan at that instant), so the row may not exist.
    # ensure_subscription() is idempotent — safe to call unconditionally,
    # never assume the signal's row is there — then shorten to the
    # self-signup length and pin the chosen plan.
    cfg = BillingSettings.load()
    sub = billing_svc.ensure_subscription(project, plan=plan, trial_days=cfg.self_signup_trial_days)
    if plan is not None and sub.plan_id != plan.pk:
        sub.plan = plan
        sub.save(update_fields=["plan", "updated_at"])
    billing_svc.reset_trial(sub, cfg.self_signup_trial_days)

    ref_code = (ref_code or "").strip()[:16]
    if ref_code:
        referrer = Profile.objects.select_related("user").filter(
            affiliate_code=ref_code, platform_role=PlatformRole.MANAGER,
            is_banned=False, user__is_active=True,
        ).first()
        if referrer is not None:
            sub.referred_by = referrer.user
            sub.affiliate_ref = ref_code
            sub.save(update_fields=["referred_by", "affiliate_ref", "updated_at"])

    record_audit(
        actor=user, project=project, action=AuditLog.Action.CREATE, target=project,
        changes={"signup": "self-serve", "owner": email}, request=request,
    )

    # Fill the storefront with editable demo content, same as a staff-made
    # store, then overwrite the demo contact phone with the real signup number
    # (this callback is registered last, so it runs after the seed).
    from apps.control.store_services import _seed_demo

    transaction.on_commit(lambda: _seed_demo(project.pk))
    transaction.on_commit(
        lambda: _fill_contact_info(project.pk, phone, city, state, postal_code)
    )

    return project, user, existing is None


@transaction.atomic
def start_self_signup(*, name, email, store_name, phone, password, plan=None,
                      ref_code="", city="", state="", postal_code="",
                      signup_source=None, capi_context=None, request=None):
    """Password-based alternative to the Google-OAuth signup — begins the
    same flow as ``self_signup`` but doesn't create anything yet. Unlike
    Google (whose own ``email_verified`` claim is proof enough), a typed-in
    email/password has no other confirmation that the signer-upper owns that
    address, so the account/store/membership/subscription are only ever
    created by ``confirm_self_signup``, once they click the emailed link.

    The plaintext password never touches the DB — only its hash, inside the
    pending row's JSON payload. Returns the ``PendingVerification``.
    """
    email = (email or "").strip().lower()
    store_name = (store_name or "").strip()
    phone = (phone or "").strip()
    if not email:
        raise ValidationError("Email is required.")
    if not store_name:
        raise ValidationError("Store name is required.")
    if not phone:
        raise ValidationError("Mobile number is required.")
    if not password:
        raise ValidationError("A password is required.")

    existing = User.objects.filter(email__iexact=email).first()
    if existing and existing.has_usable_password():
        raise ValidationError(
            "An account with this email already exists — sign in instead."
        )

    PendingVerification.objects.filter(
        kind=PendingVerification.Kind.SELF_SIGNUP, email=email, consumed_at__isnull=True,
    ).delete()
    pending = PendingVerification.objects.create(
        kind=PendingVerification.Kind.SELF_SIGNUP, email=email,
        expires_at=timezone.now() + SIGNUP_VERIFICATION_WINDOW,
        payload={
            "name": (name or "").strip(),
            "store_name": store_name,
            "phone": phone,
            "password_hash": make_password(password),
            "plan": plan.code if plan else "",
            "ref_code": (ref_code or "").strip()[:16],
            "city": city, "state": state, "postal_code": postal_code,
            "signup_source": signup_source or {},
            "capi": capi_context or {},
        },
    )
    confirm_url = absolute_url(request, reverse("accounts:signup_verify", args=[pending.token]))
    transaction.on_commit(lambda: _send_signup_verification(pending.pk, confirm_url))
    return pending


def _send_signup_verification(pending_id, confirm_url):
    from .tasks import send_signup_verification_email

    send_signup_verification_email.delay(pending_id, confirm_url)


def resend_self_signup_verification(*, email, request=None):
    """Always behaves the same whether or not a pending signup exists for
    this address — avoids leaking which emails have started signing up."""
    email = (email or "").strip().lower()
    pending = PendingVerification.objects.filter(
        kind=PendingVerification.Kind.SELF_SIGNUP, email=email, consumed_at__isnull=True,
    ).order_by("-created_at").first()
    if pending is None or pending.is_expired:
        return
    from .models import _new_verification_token

    pending.token = _new_verification_token()
    pending.expires_at = timezone.now() + SIGNUP_VERIFICATION_WINDOW
    pending.save(update_fields=["token", "expires_at", "updated_at"])
    confirm_url = absolute_url(request, reverse("accounts:signup_verify", args=[pending.token]))
    transaction.on_commit(lambda: _send_signup_verification(pending.pk, confirm_url))


def confirm_self_signup(*, pending, request=None):
    """The signer-upper clicked their confirmation link — only now does
    ``self_signup`` actually run. Returns ``(project, user, user_was_created)``."""
    if pending.is_used:
        raise ValidationError("This confirmation link has already been used.")
    if pending.is_expired:
        raise ValidationError("This confirmation link has expired — request a new one.")

    payload = pending.payload
    plan = Plan.objects.filter(
        is_active=True, is_public=True, code=payload.get("plan") or ""
    ).first()
    project, user, created = self_signup(
        name=payload.get("name", ""), email=pending.email,
        store_name=payload.get("store_name", ""), phone=payload.get("phone", ""),
        password_hash=payload.get("password_hash"), plan=plan, oauth=False,
        request=request, ref_code=payload.get("ref_code", ""),
        city=payload.get("city", ""), state=payload.get("state", ""),
        postal_code=payload.get("postal_code", ""),
    )
    pending.consumed_at = timezone.now()
    pending.save(update_fields=["consumed_at", "updated_at"])
    return project, user, created


def _fill_contact_info(project_id, phone, city="", state="", postal_code=""):
    from apps.cms.models import StoreProfile

    defaults = {"support_phone": phone, "whatsapp": phone}
    if city:
        defaults["city"] = city.strip()
    if state:
        defaults["state"] = state.strip()
    if postal_code:
        defaults["postal_code"] = postal_code.strip()
    StoreProfile.objects.update_or_create(project_id=project_id, defaults=defaults)
