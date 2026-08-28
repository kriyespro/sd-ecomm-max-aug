"""Shared helpers for the server-rendered storefront.

Everything is scoped to ``request.project`` (resolved from the Host header by
ProjectResolverMiddleware). The cart is keyed by the Django session.
"""

from django.http import Http404

from apps.cart import services as cart_svc
from apps.categories.models import Category
from apps.cms.models import Page, ThemeSettings


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


def _announcement(project):
    return next(
        (b for b in project.banners.filter(placement="announcement").order_by("priority") if b.is_live),
        None,
    )


def base_context(request, project):
    cart = get_cart(request, project)
    theme = ThemeSettings.objects.filter(project=project).first()
    return {
        "store": project,
        "nav_categories": Category.objects.filter(project=project, is_active=True)[:8],
        "footer_pages": [
            p for p in Page.objects.filter(project=project).only(
                "title", "slug", "status", "published_at", "kind"
            ) if p.is_live
        ],
        "cart": cart,
        "cart_count": cart.item_count,
        "theme": theme,
        "primary_color": theme.primary_color if theme else "#0f172a",
        "announcement": _announcement(project),
    }
