"""CMS read helpers: storefront config, navigation trees, page/banner payloads.

These produce plain dicts so both the (current) lightweight JSON views and the
Phase 10 DRF layer can reuse them.
"""

from .models import Banner, Menu, MenuItem, Page, ThemeSettings


def _theme(project):
    theme = ThemeSettings.objects.filter(project=project).first()
    if theme is not None:
        return theme.as_dict()
    branding = project.branding or {}
    return {
        "primary": branding.get("primary", "#111111"),
        "secondary": branding.get("secondary", "#ffffff"),
        "accent": branding.get("accent", "#2563eb"),
        "fonts": {}, "layout": {}, "homepage_sections": [], "tokens": branding,
    }


def store_config(project):
    from apps.seo.models import SeoSettings

    seo = SeoSettings.objects.filter(project=project).first()
    return {
        "name": project.name,
        "logo": project.logo.url if getattr(project, "logo", None) else "",
        "favicon": project.favicon.url if getattr(project, "favicon", None) else "",
        "currency": project.currency,
        "country": project.country,
        "timezone": project.timezone,
        "status": project.status,
        "theme": _theme(project),
        "features": {
            "wishlist": project.feature_enabled("wishlist", True),
            "reviews": project.feature_enabled("reviews", True),
            "coupons": project.feature_enabled("coupons", True),
        },
        "feature_flags": project.feature_flags or {},
        "seo": {
            "title_suffix": seo.title_suffix if seo else "",
            "default_description": seo.default_description if seo else "",
            "twitter_handle": seo.twitter_handle if seo else "",
            "organization_schema": seo.organization_schema if seo else {},
            "google_site_verification": seo.google_site_verification if seo else "",
        },
        "navigation": {loc: menu_tree(project, loc) for loc in ["main", "footer", "mobile", "category"]},
        "announcement": _announcement(project),
        "pages": [
            {"kind": p.kind, "title": p.title, "slug": p.slug}
            for p in Page.objects.filter(project=project).only("kind", "title", "slug", "status", "published_at")
            if p.is_live
        ],
    }


def _announcement(project):
    banner = next(
        (b for b in Banner.objects.filter(project=project, placement="announcement").order_by("priority")
         if b.is_live),
        None,
    )
    if banner is None:
        return None
    return {"heading": banner.heading, "cta_label": banner.cta_label, "cta_url": banner.cta_url}


def _item_node(item, children_by_parent):
    return {
        "label": item.label,
        "url": item.resolved_url(),
        "new_tab": item.open_in_new_tab,
        "children": [
            _item_node(child, children_by_parent)
            for child in children_by_parent.get(item.id, [])
            if child.is_active
        ],
    }


def menu_tree(project, location):
    menu = Menu.objects.filter(project=project, location=location, is_active=True).first()
    if menu is None:
        return []
    items = list(
        MenuItem.objects.filter(menu=menu, is_active=True)
        .select_related("page", "category").order_by("order", "id")
    )
    children_by_parent = {}
    for item in items:
        children_by_parent.setdefault(item.parent_id, []).append(item)
    return [_item_node(item, children_by_parent) for item in children_by_parent.get(None, [])]


def page_payload(page):
    return {
        "kind": page.kind,
        "title": page.title,
        "slug": page.slug,
        "excerpt": page.excerpt,
        "body": page.body,
        "blocks": page.blocks or [],
        "template_key": page.template_key,
        "published_at": page.published_at.isoformat() if page.published_at else None,
    }


def active_banners(project, placement=None):
    qs = Banner.objects.filter(project=project)
    if placement:
        qs = qs.filter(placement=placement)
    return [
        {
            "name": b.name,
            "placement": b.placement,
            "image": b.image.url if b.image else "",
            "mobile_image": b.mobile_image.url if b.mobile_image else "",
            "heading": b.heading,
            "subheading": b.subheading,
            "cta_label": b.cta_label,
            "cta_url": b.cta_url,
        }
        for b in qs.order_by("placement", "priority") if b.is_live
    ]
