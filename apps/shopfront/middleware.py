"""Keep the QA storefronts always-fresh in development.

Django's dev server sends no Cache-Control on rendered pages, so browsers apply
heuristic caching and stale HTML shows up after edits. This forces no-store for
the storefront paths (only when DEBUG).
"""

from django.conf import settings

from .runtime import use_skin

_PREFIXES = ("/app/", "/demo/", "/shop/")


class NoStoreStorefrontMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        storefront = request.path.startswith(_PREFIXES) or getattr(
            request, "storefront_host", False
        )
        if settings.DEBUG and storefront:
            response["Cache-Control"] = "no-store, must-revalidate"
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
        if not (
            request.path.startswith("/app/")
            or getattr(request, "storefront_host", False)
        ):
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
