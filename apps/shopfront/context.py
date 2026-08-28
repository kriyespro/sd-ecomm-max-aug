"""Shared context + helpers for the shopfront (server-rendered, Jinja2 + HTMX).

Scoped to ``request.project`` (Host). Cart keyed by the Django session; a logged
in user's cart is keyed by the user.
"""

from decimal import Decimal

from django.http import Http404

from django.db.models import Min

from apps.cart import services as cart_svc
from apps.categories.models import Category
from apps.cms.models import Page, ThemeSettings
from apps.shipping.models import ShippingMethod


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
    theme = (
        ThemeSettings.objects.filter(project=project)
        .only("primary_color", "project_id").first()
    )

    # one query for both banner placements we care about
    banners = {}
    for b in project.banners.filter(placement__in=("announcement", "hero")).order_by("priority"):
        if b.is_live and b.placement not in banners:
            banners[b.placement] = b

    ctx = {
        "store": project,
        "skin_slug": getattr(request, "skin_slug", "default"),
        "currency": project.currency,
        "accent": (theme.primary_color if theme else "#b08d57"),
        "categories": list(Category.objects.filter(project=project, is_active=True)[:10]),
        "footer_pages": [
            p for p in Page.objects.filter(project=project).only(
                "title", "slug", "status", "published_at", "kind"
            ) if p.is_live
        ],
        "cart": cart,
        "cart_count": cart.item_count,
        "cart_subtotal": cart.subtotal,
        "free_ship_over": ShippingMethod.objects.filter(
            project=project, is_active=True, free_over__isnull=False
        ).aggregate(m=Min("free_over"))["m"],
        "announcement": banners.get("announcement"),
        "hero_banner": banners.get("hero"),
        "user": request.user,
    }
    ctx.update(extra)
    return ctx


def money(value, currency="₹"):
    try:
        return f"{currency}{Decimal(value):,.0f}"
    except Exception:  # noqa: BLE001
        return f"{currency}{value}"
