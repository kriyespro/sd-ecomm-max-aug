"""Mandatory 2FA enforcement for every Mission Control account.

Gated surface is Mission Control (``/admin/`` — see ``apps.control.navigation.
MOUNT``) for every account that can reach it: platform admin, store owner,
manager, staff, DGC. In this codebase that's exactly ``User.is_staff`` —
``apps.accounts.team`` keeps it in sync with active store membership, and
it's the same flag ``ControlAccessMixin`` requires for every Mission
Control view. A non-staff account (a storefront shopper) never has it and
is never touched by this gate.

An account without a confirmed TOTP secret is redirected to setup on its
next Mission Control request until it finishes. This does not force a
logout of already-active sessions — there is no way to demand a code from
a session that predates 2FA without a forced logout of every account at
once, which is its own outage risk.

Exception: a brand-new signup gets one grace session (see
apps.accounts.twofactor.grant_signup_grace) — no setup wall on the very
first login right after creating the account, but the next real login
(a fresh session) is enforced normally.
"""

from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from .twofactor import has_signup_grace, is_enabled

_GATED_PREFIX = "/admin/"
# Reachable under /admin/ without a confirmed 2FA setup — none today, but
# kept as an explicit hook rather than hardcoding the setup path elsewhere.
_EXEMPT_PREFIXES = ()


class TwoFactorEnforcementMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            request.path.startswith(_GATED_PREFIX)
            and not request.path.startswith(_EXEMPT_PREFIXES)
            and user and user.is_authenticated and user.is_staff
            and not is_enabled(user)
            and not has_signup_grace(request)
        ):
            return redirect(reverse("accounts:2fa_setup"))
        return self.get_response(request)


IDLE_TIMEOUT_SECONDS = 35 * 60
_LAST_SEEN_KEY = "_idle_last_seen"


class IdleLogoutMiddleware:
    """Mission Control only (``/admin/``) — a staff session with no request
    in the last 35 minutes is logged out on its next request, not on a
    fixed clock. Every ``/admin/`` request stamps ``_LAST_SEEN_KEY``, so
    active use (clicking around, htmx polling) keeps a session alive
    indefinitely; only genuine idle time ends it. Storefront shoppers are
    untouched — same is_staff boundary as the 2FA gate."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if request.path.startswith(_GATED_PREFIX) and user and user.is_authenticated and user.is_staff:
            now = timezone.now().timestamp()
            last_seen = request.session.get(_LAST_SEEN_KEY)
            if last_seen is not None and now - last_seen > IDLE_TIMEOUT_SECONDS:
                next_url = request.get_full_path()
                logout(request)
                messages.info(request, "You were signed out after 35 minutes of inactivity.")
                return redirect(f"{reverse('accounts:login')}?next={next_url}")
            request.session[_LAST_SEEN_KEY] = now
        return self.get_response(request)
