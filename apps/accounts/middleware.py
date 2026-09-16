"""Mandatory 2FA enforcement for platform admins.

Gated surface is Mission Control (``/admin/`` — see ``apps.control.navigation.
MOUNT``) — that's where platform-admin privilege is actually exercised
(billing, the user directory, impersonation, every store). A superuser
session touching the public storefront or the API incidentally (a skin
preview, a webhook) is not what this is protecting.

A platform admin (superuser or Platform Owner — the highest-privilege
account on the whole platform) who has not yet confirmed a TOTP secret is
redirected to setup on their next Mission Control request until they do.
This does not force a logout of already-active sessions — there is no way
to demand a code from a session that predates 2FA without a forced logout
of every admin, which is its own outage risk.
"""

from django.shortcuts import redirect
from django.urls import reverse

from .permissions import is_platform_admin
from .twofactor import is_enabled

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
            and user and user.is_authenticated
            and is_platform_admin(user)
            and not is_enabled(user)
        ):
            return redirect(reverse("accounts:2fa_setup"))
        return self.get_response(request)
