"""Minimal Google OAuth 2.0 (authorization-code) for public self-signup.

Stdlib only, same house style as ``apps/billing/razorpay.py``. We read the
profile from the ``id_token`` returned by Google's token endpoint over TLS in
the code exchange — Google's docs allow skipping local signature verification
when the token is obtained that way, so we just sanity-check the claims.
"""

import base64
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.urls import reverse

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - public endpoint
_SCOPE = "openid email profile"
_ISS = {"accounts.google.com", "https://accounts.google.com"}

SESSION_KEY = "google_oauth_flow"


class OAuthError(Exception):
    pass


def is_enabled():
    return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)


def redirect_uri(request):
    return settings.GOOGLE_OAUTH_REDIRECT_URI or request.build_absolute_uri(
        reverse("accounts:google_callback")
    )


def start(request, *, plan="", next_url=""):
    """Stash a CSRF ``state`` in the session, return the Google consent URL."""
    state = secrets.token_urlsafe(24)
    request.session[SESSION_KEY] = {"state": state, "plan": plan or "", "next": next_url or ""}
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri(request),
        "response_type": "code",
        "scope": _SCOPE,
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{_AUTH_URL}?{urllib.parse.urlencode(params)}"


def _b64url_json(segment):
    pad = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + pad))


def exchange_code(request, code):
    """Trade the auth code for the caller's Google profile.

    Returns ``{"email", "email_verified", "name", "sub"}``. Raises ``OAuthError``.
    """
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
        "redirect_uri": redirect_uri(request),
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        _TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed https host
            payload = json.loads(resp.read())
    except (urllib.error.URLError, ValueError) as exc:
        raise OAuthError("Could not reach Google to complete sign-in.") from exc

    id_token = payload.get("id_token")
    if not id_token or id_token.count(".") != 2:
        raise OAuthError("Google did not return an identity token.")
    try:
        claims = _b64url_json(id_token.split(".")[1])
    except (ValueError, json.JSONDecodeError) as exc:
        raise OAuthError("Malformed identity token from Google.") from exc

    if claims.get("iss") not in _ISS:
        raise OAuthError("Unexpected token issuer.")
    if claims.get("aud") != settings.GOOGLE_OAUTH_CLIENT_ID:
        raise OAuthError("Token was issued for a different app.")
    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise OAuthError("Google account has no email address.")
    if not claims.get("email_verified"):
        raise OAuthError("Your Google email address isn't verified.")

    return {
        "email": email,
        "email_verified": True,
        "name": claims.get("name") or "",
        "sub": claims.get("sub") or "",
    }
