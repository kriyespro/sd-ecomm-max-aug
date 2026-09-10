"""Thin Pinterest v5 + Google Business Profile API clients. stdlib only."""

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


class SocialAPIError(Exception):
    def __init__(self, message, *, status=None, retryable=False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def _request(method, url, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SocialAPIError(f"{method} {url} -> {exc.code}: {detail}",
                             status=exc.code, retryable=exc.code >= 500 or exc.code == 429) from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise SocialAPIError(f"{url} unreachable: {exc}", retryable=True) from exc


# --- Pinterest ------------------------------------------------------------

_PIN = "https://api.pinterest.com/v5"


def pinterest_user(token):
    return _request("GET", f"{_PIN}/user_account", token)


def pinterest_boards(token):
    data = _request("GET", f"{_PIN}/boards?page_size=100", token)
    return [{"id": b["id"], "name": b.get("name", b["id"])} for b in data.get("items", [])]


def pinterest_create_pin(token, *, board_id, title, description, link, image_url):
    body = {
        "board_id": board_id,
        "title": title[:100],
        "description": description[:500],
        "link": link,
        "media_source": {"source_type": "image_url", "url": image_url},
    }
    data = _request("POST", f"{_PIN}/pins", token, body)
    pin_id = data.get("id", "")
    return pin_id, (f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else "")


# --- Google Business Profile --------------------------------------------

_GBP_ACCT = "https://mybusinessaccountmanagement.googleapis.com/v1"
_GBP_INFO = "https://mybusinessbusinessinformation.googleapis.com/v1"
_GBP_V4 = "https://mybusiness.googleapis.com/v4"


def gbp_locations(token):
    accts = _request("GET", f"{_GBP_ACCT}/accounts", token).get("accounts", [])
    out = []
    for acc in accts:
        name = acc["name"]  # accounts/123
        locs = _request(
            "GET",
            f"{_GBP_INFO}/{name}/locations?readMask=name,title&pageSize=100",
            token,
        ).get("locations", [])
        for loc in locs:
            out.append({"id": f"{name}/{loc['name']}", "name": loc.get("title", loc["name"])})
    return out


def gbp_create_post(token, *, location, summary, link, image_url):
    body = {
        "languageCode": "en",
        "summary": summary[:1400],
        "callToAction": {"actionType": "SHOP", "url": link},
        "topicType": "STANDARD",
    }
    if image_url:
        body["media"] = [{"mediaFormat": "PHOTO", "sourceUrl": image_url}]
    data = _request("POST", f"{_GBP_V4}/{location}/localPosts", token, body)
    name = data.get("name", "")
    return name, data.get("searchUrl", "")
