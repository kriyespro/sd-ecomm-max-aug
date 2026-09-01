"""Curated, read-only data contract for sandboxed (uploaded) skins.

Takes the trusted Django render context (full of ORM objects) and flattens it to
plain dicts / lists / scalars — the "theme objects" documented in
``templates/shopfront/skins/THEME_GUIDE.md``. Uploaded templates never touch a
model instance, so they cannot call ``.delete()``, walk relations, or reach
Python internals even if the sandbox were bypassed.
"""

import re

from django.urls import reverse

from apps.cms.models import ThemeSettings

_CURRENCY = "₹"
_CSS_STRIP = re.compile(r"[<>]")


class _NS:
    """Attribute-and-item access over a mapping, with no dict methods to shadow
    keys (so ``cart.items`` returns the list, not ``dict.items``). Read-only."""

    __slots__ = ("__data__",)

    def __init__(self, data):
        object.__setattr__(self, "__data__", data)

    def __getattr__(self, key):
        data = object.__getattribute__(self, "__data__")
        if key in data:
            return data[key]
        raise AttributeError(key)

    def __getitem__(self, key):
        return object.__getattribute__(self, "__data__")[key]

    def __contains__(self, key):
        return key in object.__getattribute__(self, "__data__")

    def __iter__(self):
        return iter(object.__getattribute__(self, "__data__"))

    def __bool__(self):
        return bool(object.__getattribute__(self, "__data__"))

    def get(self, key, default=None):
        return object.__getattribute__(self, "__data__").get(key, default)


def _wrap(value):
    if isinstance(value, dict):
        return _NS({k: _wrap(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return [_wrap(v) for v in value]
    return value


# --- leaf serialisers ------------------------------------------------

def _image(i):
    try:
        return {
            "url": i.image.url,
            "alt": i.alt or "",
            "srcset": getattr(i, "srcset", "") or "",
            "width": getattr(i, "width", None),
            "height": getattr(i, "height", None),
        }
    except Exception:  # noqa: BLE001
        return {"url": None, "alt": "", "srcset": "", "width": None, "height": None}


def _product(p, *, in_stock=None, available_qty=None, with_variants=False):
    imgs = list(p.images.all())
    price = p.price or 0
    current = p.current_price or price
    on_sale = bool(p.on_sale)
    cat = None
    if p.category_id:
        cat = {
            "name": p.category.name,
            "slug": p.category.slug,
            "url": reverse("shopfront:shop") + f"?category={p.category.slug}",
        }
    # Variants are only rendered on the product / quick-view pages — skip the
    # extra query on listing grids.
    variants = []
    if with_variants:
        variants = [
            {
                "id": v.id,
                "label": v.name,
                "price": v.price or price,
                "current_price": v.effective_price,
                "in_stock": (v.stock or 0) > 0,
            }
            for v in p.variants.all()
            if getattr(v, "is_active", True)
        ]
    return {
        "title": p.title,
        "slug": p.slug,
        "url": reverse("shopfront:product", kwargs={"slug": p.slug}),
        "sku": p.sku,
        "price": price,
        "current_price": current,
        "compare_at_price": price if on_sale else None,
        "on_sale": on_sale,
        "discount_pct": int(round((1 - (current / price)) * 100)) if on_sale and price else 0,
        "is_new_arrival": bool(p.is_new_arrival),
        "short_description": p.short_description or "",
        "description_html": p.description or "",
        "brand": {"name": p.brand.name} if p.brand_id else None,
        "category": cat,
        "images": [_image(i) for i in imgs],
        "options": [],
        "variants": variants,
        "rating_avg": float(p.rating_avg or 0),
        "rating_count": int(p.rating_count or 0),
        "in_stock": True if in_stock is None else bool(in_stock),
        "available_qty": available_qty,
    }


def _cart(cart):
    items = []
    for it in cart.items.select_related("product", "variant").prefetch_related("product__images"):
        imgs = list(it.product.images.all())
        items.append({
            "item_id": it.id,
            "title": it.product.title,
            "slug": it.product.slug,
            "url": reverse("shopfront:product", kwargs={"slug": it.product.slug}),
            "image_url": imgs[0].image.url if imgs else None,
            "variant_label": it.variant.name if it.variant_id else "",
            "unit_price": it.unit_price,
            "quantity": it.quantity,
            "line_total": it.line_total,
        })
    return {
        "item_count": cart.item_count,
        "subtotal": cart.subtotal,
        "currency": _CURRENCY,
        "items": items,
    }


def _order(o):
    items = []
    for oi in o.items.all():
        img = None
        if oi.product_id:
            imgs = list(oi.product.images.all()[:1])
            img = imgs[0].image.url if imgs else None
        items.append({
            "title": oi.product_title,
            "variant_label": oi.variant_name or "",
            "quantity": oi.quantity,
            "unit_price": oi.unit_price,
            "line_total": oi.line_total,
            "image_url": img,
        })
    tracking = None
    if o.tracking_number:
        tracking = {
            "carrier": o.courier or "",
            "number": o.tracking_number,
            "url": "",
            "events": [
                {"at": e.created_at, "status": getattr(e, "status", ""),
                 "note": getattr(e, "note", "") or getattr(e, "message", "")}
                for e in o.events.all()
            ],
        }
    return {
        "number": o.number,
        "status": o.status,
        "status_label": o.get_status_display(),
        "placed_at": o.placed_at,
        "items": items,
        "subtotal": o.subtotal,
        "shipping_total": o.shipping_total,
        "discount_total": o.discount_total,
        "grand_total": o.grand_total,
        "shipping_address": o.shipping_address or {},
        "tracking": tracking,
    }


def _store(request, project, ctx, theme):
    logo_url = None
    try:
        if project.logo:
            logo_url = project.logo.url
    except Exception:  # noqa: BLE001
        pass
    ann = ctx.get("announcement")
    hero = ctx.get("hero_banner")
    return {
        "name": project.name,
        "currency": _CURRENCY,
        "logo_url": logo_url,
        "accent": (theme.primary_color if theme else "#111111"),
        "font_body": (theme.font_body if theme else ""),
        "font_heading": (theme.font_heading if theme else ""),
        "custom_css": _CSS_STRIP.sub("", (theme.custom_css if theme else "") or ""),
        "menu": [
            {"label": c.name,
             "url": reverse("shopfront:shop") + f"?category={c.slug}",
             "children": []}
            for c in ctx.get("categories", [])
        ],
        "footer_links": [
            {"title": p.title, "url": reverse("shopfront:page", kwargs={"slug": p.slug})}
            for p in ctx.get("footer_pages", [])
        ],
        "announcement": ({"text": getattr(ann, "text", "") or getattr(ann, "heading", ""),
                          "url": getattr(ann, "link_url", "") or getattr(ann, "cta_url", "")}
                         if ann else None),
        "hero": ({"heading": getattr(hero, "heading", ""),
                  "subheading": getattr(hero, "subheading", ""),
                  "image_url": (hero.image.url if getattr(hero, "image", None) else None),
                  "cta_label": getattr(hero, "cta_label", ""),
                  "cta_url": getattr(hero, "cta_url", "")}
                 if hero else None),
        "social": {},
        "tracking": {},
    }


def _customer(request, ctx):
    u = request.user
    if not u.is_authenticated:
        return {"is_authenticated": False, "name": "", "email": "",
                "orders": [], "wishlist_slugs": []}
    orders = [
        {"number": o.number,
         "url": reverse("shopfront:order", kwargs={"number": o.number}),
         "placed_at": o.placed_at, "status": o.status,
         "status_label": o.get_status_display(),
         "total": o.grand_total, "item_count": o.items.count()}
        for o in ctx.get("orders", [])
    ]
    wl = ctx.get("wishlist_items") or ctx.get("wishlist_products") or []
    return {
        "is_authenticated": True,
        "name": u.get_full_name() or u.get_username(),
        "email": u.email,
        "orders": orders,
        "wishlist_slugs": [p.slug for p in wl],
    }


# --- entrypoint ----------------------------------------------------

def build(request, template_name, ctx):
    project = ctx.get("store")
    theme = (
        ThemeSettings.objects.filter(project=project).first()
        if project is not None else None
    )
    out = {
        "store": _store(request, project, ctx, theme),
        "customer": _customer(request, ctx),
        "cart": _cart(ctx["cart"]) if ctx.get("cart") is not None else None,
        "csrf_input": ctx.get("csrf_input", ""),
        "csrf_token": ctx.get("csrf_token", ""),
        "skin_slug": ctx.get("skin_slug", "default"),
        "messages": [
            {"level": m.level_tag, "text": str(m)}
            for m in ctx.get("messages", [])
        ],
    }
    free_over = ctx.get("free_ship_over")
    if out["cart"] is not None:
        out["cart"]["free_ship_over"] = free_over
        remaining = None
        if free_over is not None:
            remaining = max(0, free_over - out["cart"]["subtotal"])
        out["cart"]["free_ship_remaining"] = remaining

    # home
    for key in ("featured", "new_arrivals", "related", "recently_viewed"):
        if key in ctx:
            out[key] = [_product(p) for p in ctx[key]]
    if "cat_tiles" in ctx:
        out["category_tiles"] = [
            {"name": t["category"].name,
             "url": reverse("shopfront:shop") + f"?category={t['category'].slug}",
             "image_url": t.get("image")}
            for t in ctx["cat_tiles"]
        ]
    if "testimonials" in ctx:
        out["testimonials"] = [
            {"author": r.author_name, "body": r.body, "rating": r.rating}
            for r in ctx["testimonials"]
        ]

    # search suggest
    if "suggestions" in ctx:
        out["suggestions"] = [_product(p) for p in ctx["suggestions"]]
        out["query"] = ctx.get("query", "")

    # wishlist button fragment
    for key in ("wl_slug", "wl_active", "wl_need_login"):
        if key in ctx:
            out[key] = ctx[key]

    # listing
    if "products" in ctx:
        out["products"] = [_product(p) for p in ctx["products"]]
        page = ctx.get("page_obj")
        if page is not None:
            base = reverse("shopfront:shop")
            out["pagination"] = {
                "page": page.number,
                "pages": page.paginator.num_pages,
                "count": page.paginator.count,
                "has_prev": page.has_previous(),
                "has_next": page.has_next(),
                "prev_url": f"{base}?page={page.previous_page_number()}" if page.has_previous() else None,
                "next_url": f"{base}?page={page.next_page_number()}" if page.has_next() else None,
            }
        out["filters"] = {
            "categories": [
                {"name": c.name, "slug": c.slug,
                 "url": reverse("shopfront:shop") + f"?category={c.slug}"}
                for c in ctx.get("all_categories", [])
            ],
            "sorts": [
                {"key": k, "label": lbl, "selected": ctx.get("sort") == k}
                for k, lbl in (("new", "Newest"), ("price_asc", "Price: low to high"),
                               ("price_desc", "Price: high to low"),
                               ("rating", "Top rated"), ("name", "Name"))
            ],
            "active_category": ctx.get("active_category", ""),
            "query": ctx.get("query", ""),
            "price_min": ctx.get("price_min", ""),
            "price_max": ctx.get("price_max", ""),
        }

    # product detail
    if "product" in ctx:
        p = ctx["product"]
        avail = ctx.get("available")
        out["product"] = _product(
            p, in_stock=(avail is None or avail > 0), available_qty=avail,
            with_variants=True,
        )
        if "reviews" in ctx:
            rq = ctx["reviews"]
            out["reviews"] = {
                "average": float(p.rating_avg or 0),
                "total": ctx.get("review_total", 0),
                "breakdown": ctx.get("rating_breakdown", []),
                "items": [
                    {"author": r.author_name, "rating": r.rating,
                     "title": r.title, "body": r.body, "created_at": r.created_at}
                    for r in rq[:20]
                ],
                "can_submit": True,
            }
        d = ctx.get("delivery")
        if d:
            out["delivery"] = {
                "min_date": d.get("min"), "max_date": d.get("max"),
                "label": d.get("label"), "free_over": d.get("free_over"),
            }
        out["review_ok"] = ctx.get("review_ok")
        out["review_msg"] = ctx.get("review_msg")

    # quick view
    if template_name.endswith("_quickview.jinja") and "product" in ctx:
        pass  # product already set above

    # checkout — available_methods() yields (method, quote) pairs
    if "shipping_methods" in ctx:
        methods = []
        for i, row in enumerate(ctx["shipping_methods"]):
            method, quote = row if isinstance(row, (tuple, list)) else (row, None)
            price = quote if quote is not None else getattr(method, "base_rate", 0)
            try:
                eta = method.estimate_label()
            except Exception:  # noqa: BLE001
                eta = f"{getattr(method, 'min_days', '')}-{getattr(method, 'max_days', '')} days"
            methods.append({
                "id": method.id, "label": method.name, "price": price,
                "eta_label": eta, "selected": i == 0,
            })
        out["shipping_methods"] = methods
    if "coupon_code" in ctx or "coupon_ok" in ctx:
        out["coupon"] = {
            "code": ctx.get("coupon_code", ""),
            "ok": ctx.get("coupon_ok"),
            "message": ctx.get("coupon_msg", ""),
            "discount": ctx.get("coupon_discount", 0),
        }

    # order / track
    if ctx.get("order") is not None:
        out["order"] = _order(ctx["order"])
    if "tracked" in ctx:
        out["tracked"] = ctx.get("tracked")
        out["track_number"] = ctx.get("track_number", "")
        out["track_email"] = ctx.get("track_email", "")

    # cms page
    if ctx.get("page") is not None:
        pg = ctx["page"]
        out["page"] = {
            "title": pg.title,
            "body_html": pg.body,
            "updated_at": pg.updated_at,
        }

    # wishlist page
    wl = ctx.get("wishlist_products")
    if wl is not None:
        out["wishlist"] = [_product(p) for p in wl]

    return {k: _wrap(v) for k, v in out.items()}
