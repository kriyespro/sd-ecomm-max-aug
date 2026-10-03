"""Storefront cache-control.

Anonymous storefront page views are identical for every visitor, so they are
marked publicly cacheable (``s-maxage``) — a CDN / reverse proxy in front of the
app can then serve them from the edge. Anything tied to a visitor (logged in, or
carrying a session = a possible cart) is marked ``private``.

For this to be safe the render must be visitor-independent: see
``apps.shopfront.context.get_cart`` (no session started on a read) and the skin
base template (CSRF token pulled from the cookie client-side, never baked into
the HTML). The CDN must be told to bypass cache when the request carries a
``sessionid`` cookie.

A brand-new visitor served a cached page has no CSRF cookie yet, so their first
add-to-cart POST would fail the double-submit check. For the (non-sensitive,
unauthenticated) cart-mutation endpoints only, a request with no CSRF cookie is
instead allowed on a strict same-origin check, and handed a CSRF cookie for
subsequent requests. Everything else keeps normal CSRF.

In DEBUG the QA storefronts are forced ``no-store`` so edits show immediately.
"""

import re
from urllib.parse import urlparse

from django.conf import settings
from django.middleware.csrf import get_token

from .runtime import use_skin

_PREFIXES = ("/app/", "/demo/", "/shop/")

# On a store's own domain (request.storefront_host) everything is the storefront
# EXCEPT these shared mounts (see config.storefront_urls).
_NON_STOREFRONT = (
    "/admin/", "/sd/", "/api/", "/accounts/", "/payments/", "/whatsapp/", "/shipping/",
    "/healthz", "/readyz", "/.well-known", "/media/", "/static/",
)

# Per-visitor storefront pages — never edge-cache even for a cookieless request
# (they render a form with a CSRF token, or personalised content).
_PRIVATE_PATHS = (
    "/cart", "/checkout", "/account", "/track", "/wishlist", "/login", "/logout",
    "/orders", "/order/",
)

# Public pages: how long the edge may serve a cached copy, and how long it may
# keep serving a stale copy while it refetches.
_EDGE_MAX_AGE = 180
_EDGE_SWR = 600

# Anonymous, non-sensitive POST endpoints reachable from a cached page.
_CART_MUTATION_PATHS = ("/cart/add/", "/cart/update/", "/cart/remove/")


def _rel_path(path):
    return path[4:] if path.startswith("/app/") else path


def _is_storefront_request(request):
    if request.path.startswith(_PREFIXES):
        return True
    return getattr(request, "storefront_host", False) and not request.path.startswith(
        _NON_STOREFRONT
    )


def _same_origin(request):
    host = request.get_host()
    origin = request.META.get("HTTP_ORIGIN")
    if origin and origin != "null":
        return urlparse(origin).netloc == host
    referer = request.META.get("HTTP_REFERER")
    if referer:
        return urlparse(referer).netloc == host
    return False


def _edge_cacheable(request, response):
    if request.method not in ("GET", "HEAD"):
        return False
    if response.status_code != 200 or response.streaming:
        return False
    if request.headers.get("HX-Request") == "true":
        return False
    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        return False
    if "sessionid" in request.COOKIES:
        return False
    if response.cookies or response.has_header("Set-Cookie"):
        return False
    path = request.path.rstrip("/") or "/"
    if any(path.startswith(p.rstrip("/")) for p in _PRIVATE_PATHS):
        return False
    return True


class NoStoreStorefrontMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        storefront = _is_storefront_request(request)

        if (
            storefront
            and request.method == "POST"
            and "csrftoken" not in request.COOKIES
            and _rel_path(request.path) in _CART_MUTATION_PATHS
            and _same_origin(request)
        ):
            # First cart action from a visitor served a cached page: no CSRF
            # cookie yet. Allow on same-origin, then hand them a cookie so every
            # later request uses the normal double-submit check.
            request.csrf_processing_done = True
            get_token(request)

        response = self.get_response(request)
        if not storefront:
            return response

        if settings.DEBUG:
            response["Cache-Control"] = "no-store, must-revalidate"
            return response

        if _edge_cacheable(request, response):
            response["Cache-Control"] = (
                f"public, max-age=0, s-maxage={_EDGE_MAX_AGE}, "
                f"stale-while-revalidate={_EDGE_SWR}"
            )
            response["X-Storefront-Cache"] = "public"
        else:
            response.setdefault("Cache-Control", "private, no-cache")
            response["X-Storefront-Cache"] = "private"
        return response


_TITLE_RE = re.compile(r"<title>.*?</title>", re.S | re.I)
_DESC_RE = re.compile(r'<meta\s+name=["\']description["\'][^>]*>', re.I)


class SeoInjectionMiddleware:
    """Splice a full SEO <head> block into every storefront HTML page —
    computed <title>/description, canonical, Open Graph, Twitter cards, robots
    and JSON-LD. The view attaches ``request._seo`` ({"type","obj","crumbs",…});
    without it the page still gets canonical + site-wide tags, and the private
    pages (cart/checkout/account/order) are marked ``noindex``.

    Injection (vs editing 18 skin templates) keeps every skin correct for free.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not _is_storefront_request(request):
            return response
        if getattr(response, "streaming", False) or response.status_code != 200:
            return response
        if request.headers.get("HX-Request") == "true":
            return response
        if "text/html" not in response.get("Content-Type", ""):
            return response
        project = getattr(request, "project", None) or None
        if project is None:
            return response

        try:
            content = response.content.decode(response.charset or "utf-8")
        except (UnicodeDecodeError, AttributeError):
            return response
        if "</head>" not in content:
            return response

        rel = _rel_path(request.path)
        seo = dict(getattr(request, "_seo", None) or {})
        probe = (rel.rstrip("/") or "/")
        if any(probe.startswith(p.rstrip("/")) for p in _PRIVATE_PATHS):
            seo["noindex"] = True

        base = (getattr(project, "public_url", "") or "").rstrip("/") \
            or f"{request.scheme}://{request.get_host()}"
        try:
            from apps.seo.head import build
            block = build(project=project, path=rel, seo=seo, base_url=base)
        except Exception:  # noqa: BLE001 — never break a page over SEO
            return response

        content = _TITLE_RE.sub("", content, count=1)
        content = _DESC_RE.sub("", content, count=1)
        content = content.replace("</head>", block + "\n</head>", 1)

        response.content = content.encode(response.charset or "utf-8")
        if response.has_header("Content-Length"):
            response["Content-Length"] = str(len(response.content))
        return response


class BeaconInjectionMiddleware:
    """Splice the first-party analytics beacon (static/shopfront/beacon.js)
    into every storefront HTML page — same "inject, don't edit 18 skin
    templates" trick as SeoInjectionMiddleware. Drives the "Visitors today" /
    funnel / live-visitors dashboard widgets (apps.analytics.services):
    storefront pages are CDN-edge-cacheable, so only a script that actually
    runs in the visitor's browser sees every real page view — a Django view
    would only see cache-miss traffic.
    """

    SNIPPET = '<script src="/static/shopfront/beacon.js" defer></script>'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not _is_storefront_request(request):
            return response
        if getattr(response, "streaming", False) or response.status_code != 200:
            return response
        if request.headers.get("HX-Request") == "true":
            return response
        if "text/html" not in response.get("Content-Type", ""):
            return response
        if getattr(request, "project", None) is None:
            return response

        try:
            content = response.content.decode(response.charset or "utf-8")
        except (UnicodeDecodeError, AttributeError):
            return response
        if "</body>" not in content:
            return response

        content = content.replace("</body>", self.SNIPPET + "\n</body>", 1)
        response.content = content.encode(response.charset or "utf-8")
        if response.has_header("Content-Length"):
            response["Content-Length"] = str(len(response.content))
        return response


class TrackingInjectionMiddleware:
    """Inject the browser pixel base tags (+ a per-page conversion event from
    ``request._tracking``) into storefront HTML, once per provider the store has
    enabled — Meta Pixel, GA4, TikTok Pixel.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not _is_storefront_request(request):
            return response
        if getattr(response, "streaming", False) or response.status_code != 200:
            return response
        if request.headers.get("HX-Request") == "true":
            return response
        if "text/html" not in response.get("Content-Type", ""):
            return response
        project = getattr(request, "project", None) or None
        if project is None:
            return response

        from apps.marketing import providers
        from apps.marketing.models import tracking_for

        active = tracking_for(project)
        if not active:
            return response

        try:
            content = response.content.decode(response.charset or "utf-8")
        except (UnicodeDecodeError, AttributeError):
            return response
        if "</head>" not in content:
            return response

        heads = "".join(
            providers.head_snippet(name, cfg) for name, cfg in active.items()
        )
        content = content.replace("</head>", heads + "</head>", 1)

        evt = getattr(request, "_tracking", None)
        if evt:
            name = evt[0]
            data = evt[1] if len(evt) > 1 else {}
            event_id = evt[2] if len(evt) > 2 else None
            bits = "".join(
                providers.event_snippet(p, cfg, name, data, event_id)
                for p, cfg in active.items()
            )
            if bits and "</body>" in content:
                content = content.replace("</body>", bits + "</body>", 1)

        response.content = content.encode(response.charset or "utf-8")
        if response.has_header("Content-Length"):
            response["Content-Length"] = str(len(response.content))
        return response


def _preview_skin(request):
    """A platform admin can force any skin via ``?preview_skin=<id>`` — used by
    the review screen before a skin is approved."""
    pk = request.GET.get("preview_skin")
    if not pk:
        return None
    user = getattr(request, "user", None)
    try:
        from apps.accounts.permissions import is_platform_admin
        from apps.cms.models import Skin
    except Exception:  # noqa: BLE001
        return None
    if user is None or not is_platform_admin(user):
        return None
    return Skin.objects.filter(pk=pk).first()


class StorefrontSkinMiddleware:
    """Bind the active storefront skin so the skin-aware Jinja environment renders
    the right template bundle — for ``/app/…`` requests and for requests on a
    store's own domain (``request.storefront_host``, served at the root).

    Placed after ``ProjectResolverMiddleware`` + ``StorefrontHostMiddleware``.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not _is_storefront_request(request):
            return self.get_response(request)

        slug, skin_obj = "default", None
        project = getattr(request, "project", None)
        if project is not None:
            try:
                preview = _preview_skin(request)
                if preview is not None:
                    slug, skin_obj = preview.slug, preview
                else:
                    slug, skin_obj = _resolve_skin(project)
            except Exception:  # noqa: BLE001 — never break rendering over a skin lookup
                slug, skin_obj = "default", None
        request.skin_slug = slug
        request.skin_obj = skin_obj
        with use_skin(slug):
            return self.get_response(request)


def _resolve_skin(project):
    """(slug, skin_obj) from the cached skin binding. ``skin_obj`` is loaded only
    for a sandboxed upload (the one case ``render.py`` needs the instance)."""
    from apps.core.store_resolver import skin_binding_for_project

    slug, skin_id, sandboxed = skin_binding_for_project(project)
    if sandboxed and skin_id:
        from apps.cms.models import Skin

        return slug, Skin.objects.filter(pk=skin_id).first()
    return slug, None


_EDIT_SKIP = ("/cart", "/checkout", "/_edit")

_editor_js_src = None


def _editor_script_src():
    """URL of the editor script, unique per file content.

    Production serves static files from WhiteNoise with a year-long max-age, so a
    fixed ``/static/shopfront/inline-edit.js`` stays cached in browsers / the CDN
    after an update and editors keep running the old script (new controls never
    appear). The manifest-hashed name (when collectstatic built one) plus a
    content-hash query string make every change a new URL.
    """
    global _editor_js_src
    if _editor_js_src is None:
        import hashlib

        from django.contrib.staticfiles import finders
        from django.templatetags.static import static

        try:
            url = static("shopfront/inline-edit.js")
        except ValueError:  # manifest storage without a collected copy
            url = "/static/shopfront/inline-edit.js"
        path = finders.find("shopfront/inline-edit.js")
        if path:
            with open(path, "rb") as fh:
                url += "?v=" + hashlib.md5(fh.read()).hexdigest()[:10]
        _editor_js_src = url
    return _editor_js_src

_EDIT_CSS = (
    "<style id=\"sd-ed-css\">"
    "body.sd-ed [data-ed]{cursor:text;transition:outline-color .12s}"
    "body.sd-ed [data-ed]:hover{outline:2px dashed #6366f1;outline-offset:3px}"
    "body.sd-ed [data-ed][data-ed-t=image]{cursor:pointer}"
    "body.sd-ed [data-ed]:empty::before{content:attr(data-ed-ph);opacity:.55;font-style:italic}"
    "body.sd-ed [data-ed][contenteditable=true]{outline:2px solid #6366f1!important;outline-offset:3px;"
    "text-transform:none;min-width:2ch}"
    "</style>"
)


class InlineEditMiddleware:
    """Owner-facing storefront inline editor (see ``apps.shopfront.inline_edit``).

    For an owner / manager / the store's DGC / a platform admin only: reads the
    ``?edit=1|0`` toggle into the session, binds edit mode around the view (so
    the ``ed()`` template global stamps ``data-ed`` markers), and splices the
    editor script into the HTML. Anyone else — every shopper, every anonymous
    CDN-cached request — takes the first early return and is never touched.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.http import HttpResponseRedirect

        from .inline_edit import SESSION_KEY, accept_handoff, can_edit_storefront, editor_for
        from .runtime import use_edit_mode, use_product_form

        token = request.GET.get("sd_edit") if request.method == "GET" else None
        if (
            not _is_storefront_request(request)
            # No session cookie and no hand-off = an anonymous, CDN-cacheable
            # visitor: leave without touching the session.
            or (not token and settings.SESSION_COOKIE_NAME not in request.COOKIES)
        ):
            return self.get_response(request)
        project = getattr(request, "project", None) or None
        probe = _rel_path(request.path).rstrip("/") or "/"
        if project is None or probe.startswith(_EDIT_SKIP):
            return self.get_response(request)

        if token and accept_handoff(request, project, token):
            return HttpResponseRedirect(request.path)  # drop the token from the URL
        if editor_for(request, project) is None:
            return self.get_response(request)

        if request.method == "GET" and request.GET.get("edit") in ("0", "1"):
            request.session[SESSION_KEY] = request.GET["edit"] == "1"
        editing = bool(request.session.get(SESSION_KEY))

        # A real login on this host can open the admin product form; a
        # hand-off (DGC / platform admin) session can't, so it keeps the
        # inline title/price editing.
        user = getattr(request, "user", None)
        product_form = bool(
            editing and user is not None and user.is_authenticated and can_edit_storefront(user, project)
        )
        with use_edit_mode(editing), use_product_form(product_form):
            response = self.get_response(request)

        if (
            request.method != "GET"
            or getattr(response, "streaming", False)
            or response.status_code != 200
            or request.headers.get("HX-Request") == "true"
            or "text/html" not in response.get("Content-Type", "")
        ):
            return response
        try:
            content = response.content.decode(response.charset or "utf-8")
        except (UnicodeDecodeError, AttributeError):
            return response
        if "</body>" not in content:
            return response

        from django.middleware.csrf import get_token
        from django.urls import reverse
        from django.utils.html import format_html

        base = request.path
        script = format_html(
            '<script src="{}" defer data-active="{}" '
            'data-csrf="{}" data-save="{}" data-image="{}" data-undo="{}" data-redo="{}" '
            'data-on="{}?edit=1" data-off="{}?edit=0" data-admin="/admin/" data-product="{}"></script>',
            _editor_script_src(), "1" if editing else "0", get_token(request),
            reverse("shopfront:edit_save"), reverse("shopfront:edit_image"),
            reverse("shopfront:edit_undo"), reverse("shopfront:edit_redo"),
            base, base,
            reverse("control:product_edit", kwargs={"pk": 0}) if product_form else "",
        )
        if editing and "</head>" in content:
            content = content.replace("</head>", _EDIT_CSS + "</head>", 1)
        content = content.replace("</body>", script + "\n</body>", 1)
        response.content = content.encode(response.charset or "utf-8")
        if response.has_header("Content-Length"):
            response["Content-Length"] = str(len(response.content))
        response["Cache-Control"] = "private, no-store"
        return response
