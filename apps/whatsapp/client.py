"""Meta WhatsApp Cloud API client. stdlib only, 10s timeout.

Docs: https://developers.facebook.com/docs/whatsapp/cloud-api
"""

import json
import logging
import re
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.facebook.com"


class WhatsAppError(Exception):
    def __init__(self, message, *, code=None, retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def normalize_msisdn(raw):
    """Digits only, no ``+``. Cloud API accepts E.164 without the plus."""
    digits = re.sub(r"\D", "", raw or "")
    return digits


def _post(url, token, body):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            err = json.loads(raw)["error"]
            msg, code = err.get("message", raw[:200]), err.get("code")
        except Exception:  # noqa: BLE001
            msg, code = raw[:200], None
        # 4xx = bad request / bad token: don't retry. 5xx / 613 rate limit: retry.
        raise WhatsAppError(f"WhatsApp API {exc.code}: {msg}", code=code,
                            retryable=exc.code >= 500 or code in (4, 80007, 130429)) from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise WhatsAppError(f"WhatsApp API unreachable: {exc}", retryable=True) from exc


def _get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise WhatsAppError(f"WhatsApp API {exc.code}: {raw[:200]}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise WhatsAppError(f"WhatsApp API unreachable: {exc}") from exc


def _base(account):
    return f"{_GRAPH}/{account.graph_version}"


def fetch_number(account):
    """Validate the credentials — returns ``{display_phone, verified_name}``."""
    data = _get(
        f"{_base(account)}/{account.phone_number_id}"
        "?fields=display_phone_number,verified_name,quality_rating",
        account.access_token,
    )
    return {
        "display_phone": data.get("display_phone_number", ""),
        "verified_name": data.get("verified_name", ""),
        "quality_rating": data.get("quality_rating", ""),
    }


def send_template(account, *, to, name, language="en_US", body_params=None):
    """Send a pre-approved template. ``body_params`` = ordered list of strings
    for the template's body ``{{1}}``, ``{{2}}``… placeholders."""
    components = []
    if body_params:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": str(p)} for p in body_params],
        })
    payload = {
        "messaging_product": "whatsapp",
        "to": normalize_msisdn(to),
        "type": "template",
        "template": {
            "name": name,
            "language": {"code": language or "en_US"},
            **({"components": components} if components else {}),
        },
    }
    data = _post(f"{_base(account)}/{account.phone_number_id}/messages",
                 account.access_token, payload)
    return _first_wamid(data)


def send_text(account, *, to, text, preview_url=False):
    """Free-form text — only valid inside the 24h customer-service window."""
    payload = {
        "messaging_product": "whatsapp",
        "to": normalize_msisdn(to),
        "type": "text",
        "text": {"body": text, "preview_url": preview_url},
    }
    data = _post(f"{_base(account)}/{account.phone_number_id}/messages",
                 account.access_token, payload)
    return _first_wamid(data)


def _first_wamid(data):
    msgs = data.get("messages") or []
    return msgs[0]["id"] if msgs and "id" in msgs[0] else ""
