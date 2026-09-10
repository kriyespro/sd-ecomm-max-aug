"""Meta Conversions API — server-side conversion events.

stdlib only. Hashes PII per Meta's spec (lowercase/trim → SHA-256 hex).
"""

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.facebook.com/v21.0"


class CAPIError(Exception):
    pass


def _sha(value):
    v = (value or "").strip().lower()
    return hashlib.sha256(v.encode()).hexdigest() if v else None


def hash_user_data(*, email="", phone="", first_name="", last_name="",
                   city="", state="", zip_code="", country="",
                   client_ip="", user_agent="", fbp="", fbc="", external_id=""):
    """Everything that must be hashed is hashed here; ip / ua / fbp / fbc go raw."""
    out = {}
    if email:
        out["em"] = [_sha(email)]
    if phone:
        digits = "".join(c for c in phone if c.isdigit())
        if digits:
            out["ph"] = [_sha(digits)]
    for key, val in (("fn", first_name), ("ln", last_name), ("ct", city),
                     ("st", state), ("zp", zip_code), ("country", country)):
        if val:
            out[key] = [_sha(val)]
    if external_id:
        out["external_id"] = [_sha(external_id)]
    if client_ip:
        out["client_ip_address"] = client_ip
    if user_agent:
        out["client_user_agent"] = user_agent
    if fbp:
        out["fbp"] = fbp
    if fbc:
        out["fbc"] = fbc
    return out


def send_event(*, pixel_id, access_token, event_name, event_id, user_data,
               custom_data=None, event_source_url="", action_source="website",
               test_event_code="", event_time=None):
    if not (pixel_id and access_token):
        raise CAPIError("Meta pixel is not configured.")
    event = {
        "event_name": event_name,
        "event_time": int(event_time or time.time()),
        "event_id": str(event_id),
        "action_source": action_source,
        "user_data": user_data or {},
    }
    if event_source_url:
        event["event_source_url"] = event_source_url
    if custom_data:
        event["custom_data"] = custom_data

    body = {"data": [event]}
    if test_event_code:
        body["test_event_code"] = test_event_code

    req = urllib.request.Request(
        f"{_GRAPH}/{pixel_id}/events?access_token={urllib.parse.quote(access_token)}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise CAPIError(f"Meta CAPI {exc.code}: {detail}") from exc
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        raise CAPIError(f"Meta CAPI unreachable: {exc}") from exc
