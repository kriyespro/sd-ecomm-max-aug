"""SEO computation: meta for any storefront object/path, structured data,
sitemap entries, redirect resolution.
"""

from django.utils import timezone

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
            canonical = f"/product/{slug}/"
            structured = product_schema(obj)
        elif obj_type == "category":
            canonical = f"/category/{slug}/"
        elif obj_type == "page":
            canonical = f"/{slug}/"

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


def product_schema(product):
    price = getattr(product, "current_price", None) or getattr(product, "price", 0)
    data = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": product.title,
        "sku": product.sku or "",
        "description": (product.short_description or product.description or "")[:500],
        "offers": {
            "@type": "Offer",
            "price": str(price),
            "priceCurrency": product.project.currency,
            "availability": "https://schema.org/InStock",
        },
    }
    if getattr(product, "brand_id", None):
        data["brand"] = {"@type": "Brand", "name": product.brand.name}
    if getattr(product, "rating_count", 0):
        data["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": str(product.rating_avg),
            "reviewCount": product.rating_count,
        }
    return data


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
    from apps.catalog.models import Product
    from apps.categories.models import Category
    from apps.cms.models import Page, PublishStatus

    now = timezone.now()
    entries = []

    for page in Page.objects.filter(project=project, show_in_sitemap=True):
        if page.is_live:
            entries.append({"loc": f"/{page.slug}/", "lastmod": page.updated_at,
                            "changefreq": "monthly", "priority": "0.6"})

    for product in Product.objects.filter(project=project, status="active", search_indexed=True):
        entries.append({"loc": f"/product/{product.slug}/", "lastmod": product.updated_at,
                        "changefreq": "weekly", "priority": "0.8"})

    for category in Category.objects.filter(project=project, is_active=True):
        entries.append({"loc": f"/category/{category.slug}/", "lastmod": category.updated_at,
                        "changefreq": "weekly", "priority": "0.5"})

    return entries


def resolve_redirect(project, path):
    return Redirect.objects.filter(project=project, from_path=path, is_active=True).first()
