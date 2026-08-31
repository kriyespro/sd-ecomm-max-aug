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

from urllib.parse import urlparse

from django.conf import settings
from django.middleware.csrf import get_token

from .runtime import use_skin

_PREFIXES = ("/app/", "/demo/", "/shop/")

# On a store's own domain (request.storefront_host) everything is the storefront
# EXCEPT these shared mounts (see config.storefront_urls).
_NON_STOREFRONT = (
    "/admin/", "/sd/", "/api/", "/accounts/", "/payments/", "/shipping/",
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
