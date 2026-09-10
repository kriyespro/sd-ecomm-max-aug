"""TikTok Events API v1.3 — server-side events.

stdlib only. Hashes PII (email / phone) SHA-256 per TikTok's spec: trim +
lowercase the email; phone as E.164 digits with a leading ``+``.
Deduped against the browser Pixel by a shared ``event_id`` (the order number).
"""

import hashlib
import json
import logging
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_ENDPOINT = "https://business-api.tiktok.com/open_api/v1.3/event/track/"


class TikTokError(Exception):
    pass


def _sha(value):
    v = (value or "").strip().lower()
    return hashlib.sha256(v.encode()).hexdigest() if v else None


def _phone_e164(phone):
    digits = "".join(c for c in (phone or "") if c.isdigit())
    return f"+{digits}" if digits else ""


def _user(contact):
    out = {}
    email = _sha(contact.get("email", ""))
    if email:
        out["email"] = email
    phone = _sha(_phone_e164(contact.get("phone", "")))
    if phone:
        out["phone"] = phone
    ext = contact.get("external_id")
    if ext:
        out["external_id"] = _sha(str(ext))
    if contact.get("client_ip"):
        out["ip"] = contact["client_ip"]
    if contact.get("user_agent"):
        out["user_agent"] = contact["user_agent"]
    return out


def send_event(*, pixel_code, access_token, event_name, event_id, contact,
               properties=None, event_source_url="", test_event_code=""):
    if not (pixel_code and access_token):
        raise TikTokError("TikTok pixel is not configured.")
    event = {
        "event": event_name,
        "event_time": int(time.time()),
        "event_id": str(event_id),
        "user": _user(contact or {}),
    }
    if properties:
        event["properties"] = properties
    if event_source_url:
        event["page"] = {"url": event_source_url}

    body = {"event_source": "web", "event_source_id": pixel_code, "data": [event]}
    if test_event_code:
        body["test_event_code"] = test_event_code

    req = urllib.request.Request(
        _ENDPOINT, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Access-Token": access_token},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise TikTokError(f"TikTok Events API {exc.code}: {detail}") from exc
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        raise TikTokError(f"TikTok Events API unreachable: {exc}") from exc

    if data.get("code") not in (0, None):
        raise TikTokError(f"TikTok Events API: {data.get('code')} {data.get('message')}")
    return data
