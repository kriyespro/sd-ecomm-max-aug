"""Render the full <head> SEO block for a storefront page.

Called by ``apps.shopfront.middleware.SeoInjectionMiddleware``: the view attaches
``request._seo`` (a dict — see ``build``), the middleware asks for the tag block
and splices it into whatever the skin produced, replacing the skin's own
``<title>`` / ``<meta name="description">``.
"""

import html
import json

from . import services as seo_svc

_OG_TYPE = {"product": "product", "home": "website", "shop": "website",
            "category": "website", "page": "article"}


def _esc(value):
    return html.escape(str(value or ""), quote=True)


def _abs(base, path):
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")


def build(*, project, path, seo, base_url):
    """``seo`` = ``{"type", "obj"?, "crumbs"?, "title"?, "description"?,
    "image"?, "robots"?, "noindex"?}``. Returns an HTML string for <head>."""
    seo = seo or {}
    kind = seo.get("type") or "page"
    obj = seo.get("obj")

    meta = seo_svc.meta_for(project, path=path, obj=obj, obj_type=kind if obj else "")

    title = seo.get("title") or meta["title"] or project.name
    description = seo.get("description") or meta["description"] or ""
    canonical = _abs(base_url, meta["canonical"] or path)
    image = _abs(base_url, seo.get("image") or meta["og"]["image"])
    robots = seo.get("robots") or meta["robots"]
    if seo.get("noindex"):
        robots = "noindex,follow"

    og_title = seo.get("title") or meta["og"]["title"] or title
    og_desc = seo.get("description") or meta["og"]["description"] or description

    out = [
        f"<title>{_esc(title)}</title>",
        f'<meta name="description" content="{_esc(description)}">',
        f'<meta name="robots" content="{_esc(robots)}">',
        f'<link rel="canonical" href="{_esc(canonical)}">',
        f'<meta property="og:type" content="{_OG_TYPE.get(kind, "website")}">',
        f'<meta property="og:site_name" content="{_esc(project.name)}">',
        f'<meta property="og:title" content="{_esc(og_title)}">',
        f'<meta property="og:description" content="{_esc(og_desc)}">',
        f'<meta property="og:url" content="{_esc(canonical)}">',
        f'<meta name="twitter:card" content="{"summary_large_image" if image else "summary"}">',
        f'<meta name="twitter:title" content="{_esc(og_title)}">',
        f'<meta name="twitter:description" content="{_esc(og_desc)}">',
    ]
    if image:
        out.append(f'<meta property="og:image" content="{_esc(image)}">')
        out.append(f'<meta name="twitter:image" content="{_esc(image)}">')

    settings_obj = seo_svc._settings(project)
    handle = getattr(settings_obj, "twitter_handle", "") if settings_obj else ""
    if handle:
        out.append(f'<meta name="twitter:site" content="{_esc(handle)}">')
    verify = getattr(settings_obj, "google_site_verification", "") if settings_obj else ""
    if verify:
        out.append(f'<meta name="google-site-verification" content="{_esc(verify)}">')
    fb_app = getattr(settings_obj, "facebook_app_id", "") if settings_obj else ""
    if fb_app:
        out.append(f'<meta property="fb:app_id" content="{_esc(fb_app)}">')

    for block in _json_ld_blocks(project, kind, obj, meta, seo, base_url):
        out.append(
            '<script type="application/ld+json">'
            + json.dumps(block, separators=(",", ":"), ensure_ascii=False)
            + "</script>"
        )
    return "\n".join(out)


def _json_ld_blocks(project, kind, obj, meta, seo, base_url):
    blocks = []
    if kind == "home":
        blocks.append(seo_svc.website_schema(project))
        blocks.append(seo_svc.organization_schema(project))
    if kind == "product" and obj is not None:
        blocks.append(seo_svc.product_schema(
            obj, available=seo.get("available"), base_url=base_url,
        ))
    elif meta.get("structured_data"):
        sd = meta["structured_data"]
        img = sd.get("image")
        if isinstance(img, str) and img and not img.startswith("http"):
            sd["image"] = _abs(base_url, img)
        blocks.append(sd)
    crumbs = seo.get("crumbs")
    if crumbs:
        blocks.append(seo_svc.breadcrumb_schema(
            [(name, _abs(base_url, url)) for name, url in crumbs]
        ))
    return blocks
