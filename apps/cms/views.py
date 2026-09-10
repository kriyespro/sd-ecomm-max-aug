"""Public storefront endpoints (headless).

Lightweight JSON + XML now; the full REST surface arrives in Phase 10. Project is
resolved from the Host header by ``ProjectResolverMiddleware`` — never trusted
from the client.
"""

from django.http import HttpResponse, JsonResponse
from django.utils.html import escape
from django.views import View

from apps.seo import services as seo

from apps.core.middleware import trusted_base_url

from . import services
from .models import Page


def _project_or_404(request):
    # ``request.project`` is a SimpleLazyObject; an unresolved Host header (bots
    # hitting a bare IP, /sitemap.xml on an unknown domain) leaves it wrapping
    # ``None``. ``or None`` forces evaluation and collapses that to a real None
    # so the callers' ``is None`` guards actually fire.
    return getattr(request, "project", None) or None


class StoreConfigView(View):
    def get(self, request):
        project = _project_or_404(request)
        if project is None:
            return JsonResponse({"detail": "Unknown store."}, status=404)
        return JsonResponse(services.store_config(project))


class NavigationView(View):
    def get(self, request, location):
        project = _project_or_404(request)
        if project is None:
            return JsonResponse({"detail": "Unknown store."}, status=404)
        return JsonResponse({"location": location, "items": services.menu_tree(project, location)})


class PageDetailView(View):
    def get(self, request, slug):
        project = _project_or_404(request)
        if project is None:
            return JsonResponse({"detail": "Unknown store."}, status=404)
        page = Page.objects.filter(project=project, slug=slug).first()
        if page is None or not page.is_live:
            return JsonResponse({"detail": "Not found."}, status=404)
        payload = services.page_payload(page)
        payload["meta"] = seo.meta_for(project, path=f"/{page.slug}/", obj=page, obj_type="page")
        return JsonResponse(payload)


_SITEMAP_CHUNK = 20000
_XML = "application/xml"


def _sitemap_state(request):
    """``(project, entries)`` or ``None`` when there is no sitemap to serve."""
    project = _project_or_404(request)
    if project is None:
        return None
    from apps.seo.models import SeoSettings

    settings_obj = SeoSettings.objects.filter(project=project).first()
    if settings_obj is not None and not settings_obj.sitemap_enabled:
        return None
    return project, seo.sitemap_entries(project)


def _urlset(base, entries):
    rows = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">']
    for e in entries:
        rows.append("<url>")
        rows.append(f"<loc>{escape(base + e['loc'])}</loc>")
        if e.get("lastmod"):
            rows.append(f"<lastmod>{e['lastmod'].date().isoformat()}</lastmod>")
        rows.append(f"<changefreq>{e.get('changefreq', 'weekly')}</changefreq>")
        rows.append(f"<priority>{e.get('priority', '0.5')}</priority>")
        for img in e.get("images", []):
            src = img if img.startswith("http") else base + img
            rows.append(f"<image:image><image:loc>{escape(src)}</image:loc></image:image>")
        rows.append("</url>")
    rows.append("</urlset>")
    return "".join(rows)


class SitemapView(View):
    """``/sitemap.xml`` — a plain urlset, or a sitemap index when the store has
    more than one chunk of URLs."""

    def get(self, request):
        state = _sitemap_state(request)
        if state is None:
            return HttpResponse(status=404)
        _, entries = state
        base = trusted_base_url(request, request.project)

        if len(entries) <= _SITEMAP_CHUNK:
            return HttpResponse(_urlset(base, entries), content_type=_XML)

        pages = (len(entries) + _SITEMAP_CHUNK - 1) // _SITEMAP_CHUNK
        rows = ['<?xml version="1.0" encoding="UTF-8"?>',
                '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for n in range(1, pages + 1):
            rows.append(f"<sitemap><loc>{escape(base)}/sitemap-{n}.xml</loc></sitemap>")
        rows.append("</sitemapindex>")
        return HttpResponse("".join(rows), content_type=_XML)


class SitemapChunkView(View):
    def get(self, request, page):
        state = _sitemap_state(request)
        if state is None:
            return HttpResponse(status=404)
        _, entries = state
        start = (page - 1) * _SITEMAP_CHUNK
        chunk = entries[start:start + _SITEMAP_CHUNK]
        if not chunk:
            return HttpResponse(status=404)
        base = trusted_base_url(request, request.project)
        return HttpResponse(_urlset(base, chunk), content_type=_XML)


class RobotsView(View):
    _DISALLOW = ("/cart/", "/checkout/", "/account/", "/order/", "/orders",
                 "/wishlist/", "/track/", "/search/", "/*?")

    def get(self, request):
        project = _project_or_404(request)
        lines = ["User-agent: *"]
        for path in self._DISALLOW:
            lines.append(f"Disallow: {path}")
        lines.append("Allow: /")
        if project is not None:
            lines.append("")
            lines.append(f"Sitemap: {trusted_base_url(request, project)}/sitemap.xml")
        return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")
