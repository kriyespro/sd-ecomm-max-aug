"""Mission Control sidebar navigation tree + breadcrumb builder.

One source of truth for the grouped sidebar (rendered as an accordion in
``base_control.jinja``) and for the automatic breadcrumb shown on every
control page, for every role. Nothing here is per-page — a view only needs to
set ``{% block page_title %}`` and it gets a correct trail.
"""

from django.urls import NoReverseMatch, reverse

MOUNT = "/admin/"

# Each section: key, label, icon, and its items as (url_name, label, icon).
# ``label`` for the "store" section is swapped for the active store's name in
# the context processor. Role gating is applied there too.
_SECTIONS = [
    ("platform", "Platform", "◆", [
        ("stores", "Stores", "\U0001f3e2"),
        ("billing", "Billing", "\U0001f4b0"),
        ("billing_plans", "Plans & pricing", "\U0001f3f7"),
        ("skin_list", "Skins", "\U0001f3ad"),
        ("users", "Users", "\U0001f464"),
        ("partner_applications", "Partners", "\U0001f91d"),
    ]),
    ("store", "Store", "▦", [
        ("dashboard", "Dashboard", "▦"),
        ("order_list", "Orders", "\U0001f9fe"),
        ("customers", "Customers", "\U0001f9d1"),
    ]),
    ("catalog", "Catalog", "\U0001f4e6", [
        ("product_list", "Products", "\U0001f4e6"),
        ("category_list", "Categories", "\U0001f5c2"),
        ("brand_list", "Brands", "\U0001f3f7"),
        ("inventory_list", "Inventory", "\U0001f4ca"),
        ("warehouse_list", "Warehouses", "\U0001f3ec"),
    ]),
    ("marketing", "Marketing", "\U0001f3af", [
        ("coupon_list", "Coupons", "\U0001f39f"),
        ("review_list", "Reviews", "★"),
        ("notification_settings", "Notifications", "\U0001f514"),
    ]),
    ("storefront", "Storefront", "\U0001f6cd", [
        ("cms_store_profile", "Store profile", "\U0001f3ea"),
        ("cms_pages", "Pages", "\U0001f4c4"),
        ("cms_banners", "Banners", "\U0001f5bc"),
        ("cms_menus", "Menus", "\U0001f9ed"),
        ("cms_theme", "Theme", "\U0001f3a8"),
        ("seo_settings", "SEO", "\U0001f50d"),
        ("media", "Media", "\U0001f5c4"),
        ("skin_upload", "Skin upload", "⬆️"),
    ]),
    ("insights", "Insights", "\U0001f4c8", [
        ("analytics", "Analytics", "\U0001f4c8"),
        ("reports", "Reports", "\U0001f4d1"),
    ]),
    ("settings", "Settings", "⚙", [
        ("shipping_zones", "Shipping", "\U0001f69a"),
        ("webhooks", "Webhooks", "\U0001f517"),
        ("payment_providers", "Payments", "\U0001f4b3"),
        ("domains", "Domains", "\U0001f310"),
        ("team", "Team", "\U0001f465"),
        ("store_plan", "Plan & billing", "\U0001f4a0"),
    ]),
]

# Items only shown to platform admins (superuser / Platform Owner / Manager).
_PLATFORM_ADMIN_ONLY = {"billing", "billing_plans", "skin_list", "users", "partner_applications"}
# Items only shown to a store owner / manager (not plain staff).
_STORE_MANAGE_ONLY = {"payment_providers", "domains", "team", "store_plan"}


def _url(name):
    try:
        return reverse(f"control:{name}")
    except NoReverseMatch:
        return None


def build_nav(*, platform_staff, platform_admin, active_project, can_manage,
              can_upload_skin):
    """Resolved, already-permission-filtered sidebar tree."""
    nav = []
    for key, label, icon, raw_items in _SECTIONS:
        if key == "platform" and not platform_staff:
            continue
        if key != "platform" and not active_project:
            continue

        items = []
        for url_name, item_label, item_icon in raw_items:
            if url_name in _PLATFORM_ADMIN_ONLY and not platform_admin:
                continue
            if url_name in _STORE_MANAGE_ONLY and not can_manage:
                continue
            if url_name == "skin_upload" and not can_upload_skin:
                continue
            href = _url(url_name)
            if href is None:
                continue
            items.append({
                "name": url_name, "label": item_label,
                "icon": item_icon, "url": href,
            })
        if not items:
            continue
        nav.append({
            "key": key,
            "label": active_project.name if key == "store" and active_project else label,
            "icon": icon,
            "items": items,
        })
    return nav


def build_breadcrumb(request, nav):
    """``[(label, href_or_None), ...]`` parent trail for the current path.

    The page itself is appended by the template from ``{% block page_title %}``.
    Returns ``(crumbs, active_section_key, active_url)``.
    """
    path = request.path or MOUNT
    dash = _url("dashboard") or MOUNT

    best = None  # (section, item)
    for section in nav:
        for item in section["items"]:
            ip = item["url"]
            if ip == dash and path != dash:
                continue
            if (path == ip or path.startswith(ip)) and (
                best is None or len(ip) > len(best[1]["url"])
            ):
                best = (section, item)

    crumbs = [("Mission Control", dash)]
    if not best:
        return crumbs, "", ""

    section, item = best
    section_href = section["items"][0]["url"]
    if section["label"] != "Mission Control":
        crumbs.append((section["label"], section_href))
    if path != item["url"]:
        crumbs.append((item["label"], item["url"]))
    return crumbs, section["key"], item["url"]
