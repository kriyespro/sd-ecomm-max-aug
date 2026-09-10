"""SEO computation: meta for any storefront object/path, structured data,
sitemap entries, redirect resolution.
"""

from .models import Redirect, SeoMeta, SeoSettings


def _settings(project):
    return SeoSettings.objects.filter(project=project).first()


def _clean(*values):
    for v in values:
        if v:
            return v
    return ""


def _apply_suffix(title, settings_obj):
    suffix = settings_obj.title_suffix if settings_obj else ""
    if title and suffix and not title.endswith(suffix):
        return f"{title}{suffix}"
    return title


def meta_for(project, *, path="", obj=None, obj_type=""):
    """Return a meta dict for a page/product/category or a bare path.

    Precedence: SeoMeta path override > object's own seo_* fields > store
    defaults.
    """
    settings_obj = _settings(project)
    override = None
    if path:
        override = SeoMeta.objects.filter(project=project, path=path).first()

    title = description = canonical = og_title = og_description = og_image = robots = ""
    structured = {}

    if obj is not None:
        title = getattr(obj, "seo_title", "") or getattr(obj, "title", "") or str(obj)
        description = _clean(
            getattr(obj, "seo_description", ""),
            getattr(obj, "short_description", ""),
            getattr(obj, "excerpt", ""),
            (getattr(obj, "description", "") or "")[:300],
        )
        slug = getattr(obj, "slug", "")
        if obj_type == "product":
            canonical = f"/p/{slug}/"
            structured = product_schema(obj)
        elif obj_type == "category":
            canonical = f"/c/{slug}/"
        elif obj_type == "page":
            canonical = f"/page/{slug}/"

    if override is not None:
        title = _clean(override.title, title)
        description = _clean(override.description, description)
        canonical = _clean(override.canonical, canonical)
        og_title = override.og_title
        og_description = override.og_description
        if override.og_image:
            og_image = override.og_image.url
        robots = override.robots
        if override.structured_data:
            structured = override.structured_data

    if settings_obj:
        description = _clean(description, settings_obj.default_description)
        if not og_image and settings_obj.default_og_image:
            og_image = settings_obj.default_og_image.url
        robots = _clean(robots, settings_obj.default_robots)

    return {
        "title": _apply_suffix(title, settings_obj),
        "description": description,
        "canonical": canonical,
        "og": {
            "title": og_title or _apply_suffix(title, settings_obj),
            "description": og_description or description,
            "image": og_image,
        },
        "robots": robots or "index,follow",
        "structured_data": structured,
    }


def website_schema(project):
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": project.name,
        "url": project.public_url or "",
    }


def organization_schema(project):
    s = _settings(project)
    data = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": project.name,
        "url": project.public_url or "",
    }
    if s and s.default_og_image:
        data["logo"] = s.default_og_image.url
    if s and s.organization_schema:
        data.update(s.organization_schema)
    return data


def product_schema(product, *, available=None, base_url=""):
    price = getattr(product, "current_price", None) or getattr(product, "price", 0)
    in_stock = not (available is not None and available <= 0)
    data = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": product.title,
        "sku": product.sku or "",
        "description": _strip_tags(product.short_description or product.description or "")[:500],
        "offers": {
            "@type": "Offer",
            "price": str(price),
            "priceCurrency": product.project.currency,
            "availability": "https://schema.org/InStock" if in_stock
            else "https://schema.org/OutOfStock",
            "url": (base_url.rstrip("/") + f"/p/{product.slug}/") if base_url else "",
        },
    }
    img = _primary_image_url(product)
    if img:
        data["image"] = (base_url.rstrip("/") + img) if (base_url and img.startswith("/")) else img
    if getattr(product, "brand_id", None):
        data["brand"] = {"@type": "Brand", "name": product.brand.name}
    if getattr(product, "rating_count", 0):
        data["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": str(product.rating_avg),
            "reviewCount": product.rating_count,
        }
    return data


def _strip_tags(value):
    from django.utils.html import strip_tags

    return strip_tags(value or "").strip()


def _primary_image_url(product):
    try:
        cache = getattr(product, "_prefetched_objects_cache", {})
        images = cache["images"] if "images" in cache else product.images.all()
        for im in images:
            if im.is_primary and im.image:
                return im.image.url
        for im in images:
            if im.image:
                return im.image.url
    except Exception:  # noqa: BLE001
        pass
    return ""


def breadcrumb_schema(crumbs):
    """``crumbs`` = [(name, url), ...]."""
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name, "item": url}
            for i, (name, url) in enumerate(crumbs)
        ],
    }


def sitemap_entries(project):
    """``[{loc, lastmod, changefreq, priority, images?}]`` — storefront-relative
    ``loc`` matching the real shopfront routes (``/p/…``, ``/c/…``, ``/page/…``).
    """
    from apps.catalog.models import Product
    from apps.categories.models import Category
    from apps.cms.models import Page

    entries = [
        {"loc": "/", "changefreq": "daily", "priority": "1.0"},
        {"loc": "/shop/", "changefreq": "daily", "priority": "0.9"},
    ]

    for page in Page.objects.filter(project=project, show_in_sitemap=True):
        if page.is_live:
            entries.append({"loc": f"/page/{page.slug}/", "lastmod": page.updated_at,
                            "changefreq": "monthly", "priority": "0.5"})

    products = (
        Product.objects.filter(project=project, status="active", search_indexed=True)
        .prefetch_related("images")
    )
    for product in products:
        images = [im.image.url for im in product.images.all() if im.image][:5]
        entries.append({
            "loc": f"/p/{product.slug}/", "lastmod": product.updated_at,
            "changefreq": "weekly", "priority": "0.8", "images": images,
        })

    for category in Category.objects.filter(project=project, is_active=True):
        entries.append({"loc": f"/c/{category.slug}/", "lastmod": category.updated_at,
                        "changefreq": "weekly", "priority": "0.6"})

    return entries


def resolve_redirect(project, path):
    return Redirect.objects.filter(project=project, from_path=path, is_active=True).first()
