"""Best-effort image grab from a public Instagram post URL.

No Meta app / Graph API: the merchant pastes a post link, we fetch the page and
read its ``og:image``, then download that image. Locked down against SSRF —
only Instagram hosts for the page, only Instagram/Facebook CDNs for the image,
no redirects off-allowlist, timeouts and size caps throughout.

Fragile by nature (a private post, or an Instagram markup change, breaks it) —
callers must treat failure as normal and fall back to a manual upload.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO

from django.core.files.base import ContentFile

_PAGE_HOSTS = {"instagram.com", "www.instagram.com", "instagr.am", "www.instagr.am"}
_IMG_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net", "cdninstagram.com", "fbcdn.net")

_TIMEOUT = 8
_MAX_PAGE = 2_000_000       # 2 MB of HTML
_MAX_IMAGE = 8_000_000      # 8 MB
_UA = "Mozilla/5.0 (compatible; ShopInADayBot/1.0)"

_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)


class InstagramError(Exception):
    pass


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect blocked", headers, fp)


_opener = urllib.request.build_opener(_NoRedirects)


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or ""


def normalize_post_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise InstagramError("Empty URL.")
    if raw.startswith("//"):
        raw = "https:" + raw
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme != "https" or parts.hostname not in _PAGE_HOSTS:
        raise InstagramError("Not a public instagram.com post URL.")
    # keep only the path (drop tracking query / fragment)
    return urllib.parse.urlunsplit(("https", parts.hostname, parts.path, "", ""))


def _get(url: str, *, limit: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    try:
        with _opener.open(req, timeout=_TIMEOUT) as resp:
            return resp.read(limit + 1)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        raise InstagramError(f"fetch failed: {exc}") from exc


def fetch_post_image(raw_url: str) -> tuple[ContentFile, str]:
    """``(ContentFile, source_url)`` for the post's main image. Raises
    ``InstagramError`` on any problem."""
    source_url = normalize_post_url(raw_url)
    html = _get(source_url, limit=_MAX_PAGE)
    if len(html) > _MAX_PAGE:
        raise InstagramError("Instagram page too large.")
    text = html.decode("utf-8", "replace")

    m = _OG_IMAGE_RE.search(text)
    if not m:
        raise InstagramError("Could not find an image on that post (is it public?).")
    img_url = m.group(1).replace("&amp;", "&")

    host = _host(img_url)
    if not (host.endswith(_IMG_HOST_SUFFIXES)):
        raise InstagramError("Post image is served from an unexpected host.")

    data = _get(img_url, limit=_MAX_IMAGE)
    if len(data) > _MAX_IMAGE:
        raise InstagramError("Post image too large.")

    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as im:
            im.verify()
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        raise InstagramError("Downloaded file is not a valid image.") from exc

    slug = source_url.rstrip("/").rsplit("/", 1)[-1] or "post"
    return ContentFile(data, name=f"ig-{slug}.jpg"), source_url
