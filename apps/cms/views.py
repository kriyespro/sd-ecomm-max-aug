"""Public storefront endpoints (headless).

Lightweight JSON + XML now; the full REST surface arrives in Phase 10. Project is
resolved from the Host header by ``ProjectResolverMiddleware`` — never trusted
from the client.
"""

from django.http import HttpResponse, JsonResponse
from django.utils.html import escape
from django.views import View

from apps.seo import services as seo

from . import services
from .models import Page


def _project_or_404(request):
    project = getattr(request, "project", None)
    return project


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


class SitemapView(View):
    def get(self, request):
        project = _project_or_404(request)
        if project is None:
            return HttpResponse(status=404)
        from apps.seo.models import SeoSettings

        settings_obj = SeoSettings.objects.filter(project=project).first()
        if settings_obj is not None and not settings_obj.sitemap_enabled:
            return HttpResponse(status=404)

        scheme = "https" if request.is_secure() else "http"
        base = f"{scheme}://{request.get_host()}"
        rows = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
                "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">"]
        for entry in seo.sitemap_entries(project):
            rows.append("<url>")
            rows.append(f"<loc>{escape(base + entry['loc'])}</loc>")
            if entry.get("lastmod"):
                rows.append(f"<lastmod>{entry['lastmod'].date().isoformat()}</lastmod>")
            rows.append(f"<changefreq>{entry['changefreq']}</changefreq>")
            rows.append(f"<priority>{entry['priority']}</priority>")
            rows.append("</url>")
        rows.append("</urlset>")
        return HttpResponse("".join(rows), content_type="application/xml")


class RobotsView(View):
    def get(self, request):
        project = _project_or_404(request)
        scheme = "https" if request.is_secure() else "http"
        base = f"{scheme}://{request.get_host()}"
        lines = ["User-agent: *", "Allow: /"]
        if project is not None:
            lines.append(f"Sitemap: {base}/sitemap.xml")
        return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")
