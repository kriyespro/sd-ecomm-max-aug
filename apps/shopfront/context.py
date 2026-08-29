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


def get_cart(request, project):
    if not request.session.session_key:
        request.session.save()
    user = request.user if request.user.is_authenticated else None
    return cart_svc.get_or_create_cart(
        project=project, user=user, session_key=request.session.session_key
    )


def base_context(request, project, **extra):
    cart = get_cart(request, project)
    # Pull the cart lines once — cart.subtotal / cart.item_count and any template
    # that iterates cart.items now reuse this instead of re-querying each time.
    cart._prefetched_objects_cache = {
        "items": list(cart.items.select_related("product", "variant"))
    }
    chrome = store_chrome(project)

    ctx = {
        "store": project,
        "skin_slug": getattr(request, "skin_slug", "default"),
        "currency": project.currency,
        "accent": chrome["accent"],
        "categories": chrome["categories"],
        "footer_pages": chrome["footer_pages"],
        "cart": cart,
        "cart_count": cart.item_count,
        "cart_subtotal": cart.subtotal,
        "free_ship_over": chrome["free_ship_over"],
        "announcement": chrome["announcement"],
        "hero_banner": chrome["hero_banner"],
        "user": request.user,
    }
    ctx.update(extra)
    return ctx


def money(value, currency="₹"):
    try:
        return f"{currency}{Decimal(value):,.0f}"
    except Exception:  # noqa: BLE001
        return f"{currency}{value}"
