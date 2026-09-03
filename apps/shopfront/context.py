"""Shared context + helpers for the shopfront (server-rendered, Jinja2 + HTMX).

Scoped to ``request.project`` (Host). Cart keyed by the Django session; a logged
in user's cart is keyed by the user.
"""

from decimal import Decimal

from django.http import Http404

from apps.cart import services as cart_svc
from apps.core.store_resolver import store_chrome


def current_project(request):
    project = getattr(request, "project", None)
    try:
        project = project or None
    except Exception:  # noqa: BLE001
        project = None
    if project is None:
        raise Http404("No store is configured for this domain.")
    return project


def get_cart(request, project, *, create=False):
    """Active cart for this request.

    ``create=False`` (the default, used by page renders) will NOT start a
    session for an anonymous visitor who has none — it hands back an unsaved
    empty cart instead, so the response stays cookie-free and edge-cacheable.
    Cart-mutating views pass ``create=True``.
    """
    user = request.user if request.user.is_authenticated else None
    if create and user is None and not request.session.session_key:
        request.session.save()
    return cart_svc.get_or_create_cart(
        project=project, user=user,
        session_key=request.session.session_key or "",
        create=create or user is not None,
    )


def base_context(request, project, **extra):
    cart = get_cart(request, project)
    # Pull the cart lines once so cart.subtotal / cart.item_count and any
    # template that iterates cart.items reuse them instead of re-querying.
    # This must be a *materialised queryset*, not a list: Django routes
    # cart.items.all() / .exists() / .count() / .select_related() through the
    # reverse manager's get_queryset(), which hands back whatever sits in
    # _prefetched_objects_cache. A bare list there breaks .exists() (checkout)
    # and .select_related() (the ornza cart context).
    if not getattr(cart, "_is_empty", False):
        cart_items = cart.items.select_related("product", "variant")
        len(cart_items)  # force evaluation -> fills cart_items._result_cache
        cart._prefetched_objects_cache = {"items": cart_items}
    chrome = store_chrome(project)

    ctx = {
        "store": project,
        "skin_slug": getattr(request, "skin_slug", "default"),
        "currency": project.currency,
        "accent": chrome["accent"],
        "store_profile": chrome["profile"],
        "store_logo": chrome["store_logo"],
        "categories": chrome["categories"],
        "footer_pages": chrome["footer_pages"],
        "store_is_demo": chrome.get("demo", False),
        "cart": cart,
        "cart_count": cart.item_count,
        "cart_subtotal": cart.subtotal,
        "free_ship_over": chrome["free_ship_over"],
        "announcement": chrome["announcement"],
        "hero_banner": chrome["hero_banner"],
        "promo_banners": chrome["promo_banners"],
        "category_banners": chrome["category_banners"],
        "product_banner": chrome["product_banner"],
        "popup_banner": chrome["popup_banner"],
        "user": request.user,
    }
    ctx.update(extra)
    return ctx


def money(value, currency="₹"):
    try:
        return f"{currency}{Decimal(value):,.0f}"
    except Exception:  # noqa: BLE001
        return f"{currency}{value}"
