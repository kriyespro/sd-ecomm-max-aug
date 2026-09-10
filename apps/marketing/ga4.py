"""GA4 Measurement Protocol — server-side events.

stdlib only. GA4 MP carries no PII (no hashing); it needs a ``client_id``, which
for a server-only event we derive deterministically from the order number so
repeat sends land on the same GA4 "user".
"""

import hashlib
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_COLLECT = "https://www.google-analytics.com/mp/collect"
_DEBUG = "https://www.google-analytics.com/debug/mp/collect"


class GA4Error(Exception):
    pass


def client_id_for(seed):
    """A stable ``<int>.<int>`` client id from any seed (the order number)."""
    h = int(hashlib.sha1(str(seed).encode()).hexdigest()[:12], 16)
    n = h % 10_000_000_000
    return f"{n}.{n}"


def send_event(*, measurement_id, api_secret, name, params, client_id, test=False):
    if not (measurement_id and api_secret):
        raise GA4Error("GA4 is not configured.")
    endpoint = _DEBUG if test else _COLLECT
    url = (f"{endpoint}?measurement_id={urllib.parse.quote(measurement_id)}"
           f"&api_secret={urllib.parse.quote(api_secret)}")
    body = {"client_id": client_id, "events": [{"name": name, "params": params or {}}]}
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise GA4Error(f"GA4 MP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GA4Error(f"GA4 MP unreachable: {exc}") from exc
    if test:
        logger.info("ga4 debug response: %s", payload[:500])
    return payload
