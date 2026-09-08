"""Shared context + helpers for the shopfront (server-rendered, Jinja2 + HTMX).

Scoped to ``request.project`` (Host). Cart keyed by the Django session; a logged
in user's cart is keyed by the user.
"""

from decimal import Decimal

from django.http import Http404

from django.urls import reverse

from apps.cart import services as cart_svc
from apps.core.store_resolver import store_chrome


def _menu_href(node):
    """Resolve a serialized main-menu node (see
    ``apps.core.store_resolver._serialize_menu_items``) to a storefront URL."""
    lt = node.get("link_type")
    slug = node.get("slug")
    if lt == "page" and slug:
        return reverse("shopfront:page", kwargs={"slug": slug})
    if lt == "category" and slug:
        return f"{reverse('shopfront:shop')}?category={slug}"
    return node.get("url") or "#"


def _nav_node(node):
    return {
        "label": node["label"],
        "url": _menu_href(node),
        "new_tab": node.get("new_tab", False),
        "children": [_nav_node(c) for c in node.get("children", [])],
    }


def primary_nav(chrome):
    """The storefront's main menu: the store's active MAIN CMS menu when it has
    items, otherwise its active categories (keeps existing stores unchanged)."""
    menu = (chrome or {}).get("main_menu")
    if menu:
        return [_nav_node(n) for n in menu]
    shop = reverse("shopfront:shop")
    return [
        {"label": c.name, "url": f"{shop}?category={c.slug}",
         "new_tab": False, "children": []}
        for c in (chrome or {}).get("categories", [])[:8]
    ]


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
        "primary_nav": primary_nav(chrome),
        "budget_bands": chrome.get("budget_bands", []),
        "instagram_items": chrome.get("instagram_items", []),
        "footer_pages": chrome["footer_pages"],
        "store_is_demo": chrome.get("demo", False),
        "cart": cart,
        "cart_count": cart.item_count,
        "cart_subtotal": cart.subtotal,
        "free_ship_over": chrome["free_ship_over"],
        "announcement": chrome["announcement"],
        "hero_banner": chrome["hero_banner"],
        "hero_slides": chrome.get("hero_slides", []),
        "category_above_hero": chrome.get("category_above_hero", False),
        "promo_banners": chrome["promo_banners"],
        "category_banners": chrome["category_banners"],
        "product_banner": chrome["product_banner"],
        "popup_banner": chrome["popup_banner"],
        "ugc_videos": chrome["ugc_videos"],
        "user": request.user,
    }
    ctx.update(extra)
    return ctx


def money(value, currency="₹"):
    try:
        return f"{currency}{Decimal(value):,.0f}"
    except Exception:  # noqa: BLE001
        return f"{currency}{value}"
