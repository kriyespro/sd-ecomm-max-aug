""""Connect with Meta" — Facebook Login for Business OAuth.

Optional. Active only when ``settings.META_APP_ID`` + ``META_APP_SECRET`` are
set (one platform-owned Meta app). The seller clicks "Connect with Meta",
authorises the app, and we pull their Pixel ID + a long-lived (~60-day) access
token so they never paste anything.

Needs Meta App Review for ``ads_management`` + ``business_management`` scopes
before Meta will return real data in production. ``is_configured()`` gates the
button so nothing half-wired shows up until then.

stdlib only.
"""

import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

_SCOPES = "ads_management,business_management"


class MetaOAuthError(Exception):
    pass


def is_configured():
    return bool(settings.META_APP_ID and settings.META_APP_SECRET)


def _graph(path):
    return f"https://graph.facebook.com/{settings.META_GRAPH_VERSION}/{path}"


def make_state():
    return secrets.token_urlsafe(24)


def auth_url(redirect_uri, state):
    params = {
        "client_id": settings.META_APP_ID,
        "redirect_uri": redirect_uri,
        "state": state,
        "response_type": "code",
        "scope": _SCOPES,
    }
    if settings.META_OAUTH_CONFIG_ID:
        params["config_id"] = settings.META_OAUTH_CONFIG_ID
    return (f"https://www.facebook.com/{settings.META_GRAPH_VERSION}/dialog/oauth?"
            + urllib.parse.urlencode(params))


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise MetaOAuthError(f"Meta OAuth {exc.code}: {detail}") from exc
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        raise MetaOAuthError(f"Meta OAuth unreachable: {exc}") from exc


def exchange_code(code, redirect_uri):
    """Auth code -> short-lived token -> long-lived (~60 day) token."""
    short = _get(_graph("oauth/access_token") + "?" + urllib.parse.urlencode({
        "client_id": settings.META_APP_ID,
        "client_secret": settings.META_APP_SECRET,
        "redirect_uri": redirect_uri,
        "code": code,
    })).get("access_token")
    if not short:
        raise MetaOAuthError("Meta did not return an access token.")
    longlived = _get(_graph("oauth/access_token") + "?" + urllib.parse.urlencode({
        "grant_type": "fb_exchange_token",
        "client_id": settings.META_APP_ID,
        "client_secret": settings.META_APP_SECRET,
        "fb_exchange_token": short,
    }))
    return longlived.get("access_token") or short


def list_pixels(access_token):
    """Every ads pixel the connected user can see: ``[{id, name}, …]``."""
    out = []
    accounts = _get(_graph("me/adaccounts") + "?" + urllib.parse.urlencode({
        "fields": "id,name",
        "access_token": access_token,
    })).get("data", [])
    for acc in accounts:
        pixels = _get(_graph(f"{acc['id']}/adspixels") + "?" + urllib.parse.urlencode({
            "fields": "id,name",
            "access_token": access_token,
        })).get("data", [])
        for px in pixels:
            out.append({"id": px["id"], "name": px.get("name") or acc.get("name") or px["id"]})
    # de-dup by id, keep first name
    seen = {}
    for px in out:
        seen.setdefault(px["id"], px)
    return list(seen.values())
