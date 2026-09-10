"""OAuth2 for Pinterest + Google Business Profile. stdlib only.

Per-seller app: each ``SocialAccount`` carries its own ``client_id`` /
``client_secret``. We just drive the authorization-code flow and refresh.
"""

import base64
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

_PINTEREST = {
    "authorize": "https://www.pinterest.com/oauth/",
    "token": "https://api.pinterest.com/v5/oauth/token",
    "scope": "boards:read,pins:read,pins:write,user_accounts:read",
    "basic_auth": True,
}
_GBP = {
    "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
    "token": "https://oauth2.googleapis.com/token",
    "scope": "https://www.googleapis.com/auth/business.manage",
    "basic_auth": False,
    "extra_auth": {"access_type": "offline", "prompt": "consent"},
}
_CONF = {"pinterest": _PINTEREST, "gbp": _GBP}


class OAuthError(Exception):
    pass


def make_state():
    return secrets.token_urlsafe(24)


def authorize_url(provider, *, client_id, redirect_uri, state):
    conf = _CONF[provider]
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": conf["scope"],
        "state": state,
    }
    params.update(conf.get("extra_auth", {}))
    return conf["authorize"] + "?" + urllib.parse.urlencode(params)


def _token_request(provider, data, *, client_id, client_secret):
    conf = _CONF[provider]
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if conf["basic_auth"]:
        raw = f"{client_id}:{client_secret}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    else:
        data = {**data, "client_id": client_id, "client_secret": client_secret}
    req = urllib.request.Request(
        conf["token"], data=urllib.parse.urlencode(data).encode(),
        method="POST", headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise OAuthError(f"{provider} token {exc.code}: "
                         f"{exc.read().decode('utf-8', 'replace')[:200]}") from exc
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        raise OAuthError(f"{provider} token unreachable: {exc}") from exc
    if "access_token" not in payload:
        raise OAuthError(f"{provider}: no access token in response")
    return payload


def exchange_code(provider, *, code, redirect_uri, client_id, client_secret):
    return _norm(_token_request(
        provider,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        client_id=client_id, client_secret=client_secret,
    ))


def refresh(provider, *, refresh_token, client_id, client_secret):
    out = _norm(_token_request(
        provider,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
        client_id=client_id, client_secret=client_secret,
    ))
    # Google usually omits a new refresh_token — keep the old one.
    out["refresh_token"] = out["refresh_token"] or refresh_token
    return out


def _norm(payload):
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", ""),
        "scope": payload.get("scope", ""),
        "expires_at": time.time() + int(payload.get("expires_in", 3600)),
    }
