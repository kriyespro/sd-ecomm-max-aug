"""Mission Control sidebar navigation tree + breadcrumb builder.

One source of truth for the grouped sidebar (rendered as an accordion in
``base_control.jinja``) and for the automatic breadcrumb shown on every
control page, for every role. Nothing here is per-page — a view only needs to
set ``{% block page_title %}`` and it gets a correct trail.
"""

from functools import lru_cache

from django.urls import NoReverseMatch, reverse

MOUNT = "/admin/"

# Each section: key, label, icon, and its items as (url_name, label, icon).
# ``label`` for the "store" section is swapped for the active store's name in
# the context processor. Role gating is applied there too.
_SECTIONS = [
    ("platform", "Platform", "◆", [
        ("stores", "Stores", "\U0001f3e2"),
        ("my_commissions", "My earnings", "\U0001f4b5"),
        ("billing", "Billing", "\U0001f4b0"),
        ("billing_plans", "Plans & pricing", "\U0001f3f7"),
        ("skin_list", "Skins", "\U0001f3ad"),
        ("users", "Users", "\U0001f464"),
        ("partner_applications", "Partners", "\U0001f91d"),
        ("showcase_list", "Live stores", "\U0001f31f"),
        ("platform_tracking", "Marketing pixels", "\U0001f4c8"),
        ("platform_backups", "Backup & restore", "\U0001f4be"),
    ]),
    ("store", "Store", "▦", [
        ("dashboard", "Dashboard", "▦"),
        ("onboarding", "Setup guide", "\U0001f680"),
        ("order_list", "Orders", "\U0001f9fe"),
        ("customers", "Customers", "\U0001f9d1"),
    ]),
    ("catalog", "Catalog", "\U0001f4e6", [
        ("product_list", "Products", "\U0001f4e6"),
        ("product_import", "Import products", "\U0001f4e5"),
        ("media", "Media", "\U0001f5c4"),
        ("category_list", "Categories", "\U0001f5c2"),
        ("brand_list", "Brands", "\U0001f3f7"),
        ("inventory_list", "Inventory", "\U0001f4ca"),
        ("warehouse_list", "Warehouses", "\U0001f3ec"),
    ]),
    ("marketing", "Marketing", "\U0001f3af", [
        ("coupon_list", "Coupons", "\U0001f39f"),
        ("review_list", "Reviews", "★"),
        ("notification_settings", "Notifications", "\U0001f514"),
        ("tracking", "Pixels & tracking", "\U0001f4c8"),
        ("social", "Auto-share", "\U0001f4e3"),
    ]),
    ("storefront", "Storefront", "\U0001f6cd", [
        ("cms_store_profile", "Store profile", "\U0001f3ea"),
        ("cms_pages", "Pages", "\U0001f4c4"),
        ("cms_banners", "Banners", "\U0001f5bc"),
        ("cms_ugc_videos", "Shorts / videos", "\U0001f3ac"),
        ("cms_budget_bands", "Shop by budget", "\U0001f4b0"),
        ("cms_instagram", "Instagram feed", "\U0001f4f8"),
        ("cms_menus", "Menus", "\U0001f9ed"),
        ("cms_theme", "Theme", "\U0001f3a8"),
        ("seo_settings", "SEO", "\U0001f50d"),
        ("skin_upload", "Skin upload", "⬆️"),
    ]),
    ("insights", "Insights", "\U0001f4c8", [
        ("analytics", "Analytics", "\U0001f4c8"),
        ("reports", "Reports", "\U0001f4d1"),
    ]),
    ("settings", "Settings", "⚙", [
        ("shipping_zones", "Shipping", "\U0001f69a"),
        ("webhooks", "Webhooks", "\U0001f517"),
        ("whatsapp", "WhatsApp", "\U0001f4ac"),
        ("payment_providers", "Payments", "\U0001f4b3"),
        ("domains", "Domains", "\U0001f310"),
        ("team", "Team", "\U0001f465"),
        ("store_plan", "Plan & billing", "\U0001f4a0"),
        ("store_showcase", "Live store listing", "\U0001f31f"),
        ("owner_backup", "Backup & restore", "\U0001f4be"),
    ]),
    ("b2b", "B2B / Wholesale", "\U0001f91d", [
        ("b2b_settings", "Sell B2B", "\U0001f4e6"),
        ("b2b_marketplace", "Marketplace", "\U0001f6d2"),
        ("b2b_orders", "Orders to fulfill", "\U0001f69a"),
        ("b2b_payables", "What you owe", "\U0001f4b8"),
    ]),
]

# Items only shown to platform admins (superuser / Platform Owner / Manager).
_PLATFORM_ADMIN_ONLY = {"billing", "billing_plans", "skin_list", "users",
                        "partner_applications", "showcase_list", "platform_backups",
                        "platform_tracking"}
# Items only for a DGC (platform manager) — an admin has the fuller view elsewhere.
_DGC_ONLY = {"my_commissions"}
# Items only shown to a store owner / manager (not plain staff).
_STORE_MANAGE_ONLY = {"payment_providers", "domains", "team", "onboarding", "tracking", "whatsapp", "social"}
# Owner only — not even a manager. B2B/wholesale moves money between stores.
_OWNER_ONLY = {"b2b_settings", "b2b_marketplace", "b2b_orders", "b2b_payables",
               "store_showcase", "owner_backup"}
# Hidden from a store's DGC — orders, customers and money belong to the store.
_STORE_DATA_ONLY = {"order_list", "customers", "analytics", "reports", "payment_providers"}
# Billing self-service — hidden when a DGC owns the billing relationship.
_BILLING_ONLY = {"store_plan"}


@lru_cache(maxsize=1)
def _resolved_sections():
    """``_SECTIONS`` with every item's URL reversed once. URLconf is static per
    process, so this runs on the first control request and never again — the
    context processor is on every ``/admin/`` hit, HTMX polls included."""
    out = []
    for key, label, icon, raw_items in _SECTIONS:
        items = []
        for url_name, item_label, item_icon in raw_items:
            try:
                href = reverse(f"control:{url_name}")
            except NoReverseMatch:
                continue
            items.append({
                "name": url_name, "label": item_label,
                "icon": item_icon, "url": href,
            })
        out.append((key, label, icon, items))
    return out


@lru_cache(maxsize=1)
def dashboard_url():
    try:
        return reverse("control:dashboard")
    except NoReverseMatch:
        return MOUNT


def build_nav(*, platform_staff, platform_admin, active_project, can_manage,
              can_upload_skin, can_manage_billing=True, can_manage_owner=False,
              store_data_ok=True):
    """Permission-filtered sidebar tree (URLs come pre-resolved and cached)."""
    nav = []
    for key, label, icon, resolved_items in _resolved_sections():
        if key == "platform" and not platform_staff:
            continue
        if key != "platform" and not active_project:
            continue

        items = [
            it for it in resolved_items
            if not (it["name"] in _PLATFORM_ADMIN_ONLY and not platform_admin)
            and not (it["name"] in _DGC_ONLY and platform_admin)
            and not (it["name"] in _STORE_MANAGE_ONLY and not can_manage)
            and not (it["name"] in _OWNER_ONLY and not can_manage_owner)
            and not (it["name"] in _STORE_DATA_ONLY and not store_data_ok)
            and not (it["name"] in _BILLING_ONLY and not can_manage_billing)
            and not (it["name"] == "skin_upload" and not can_upload_skin)
        ]
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
    dash = dashboard_url()

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
