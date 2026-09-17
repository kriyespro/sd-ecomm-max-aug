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

from django.shortcuts import redirect
from django.urls import reverse

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
