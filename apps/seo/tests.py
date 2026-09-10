"""Storefront SEO: head-tag injection, sitemap, robots, IndexNow."""

from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from apps.catalog.models import Product
from apps.categories.models import Category
from apps.projects.models import Project
from apps.seo import services as seo_svc
from apps.seo.head import build
from apps.seo.models import SeoSettings
from apps.shopfront.middleware import SeoInjectionMiddleware

_HTML = "<!doctype html><html><head><title>Skin Title</title>" \
        '<meta name="description" content="skin desc"></head><body>hi</body></html>'


class HeadBuildTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Aurora", status="active", currency="INR")
        SeoSettings.objects.create(project=self.project, title_suffix=" | Aurora",
                                   default_description="Aurora storefront",
                                   twitter_handle="@aurora")
        self.product = Product.objects.create(
            project=self.project, title="Brass Lamp", slug="brass-lamp",
            price=Decimal("2999"), status="active",
        )

    def _build(self, seo, path="/"):
        return build(project=self.project, path=path, seo=seo,
                     base_url="https://aurora.example")

    def test_home_has_website_and_org_jsonld(self):
        out = self._build({"type": "home"})
        self.assertIn('"@type":"WebSite"', out)
        self.assertIn('"@type":"Organization"', out)
        self.assertIn('<link rel="canonical" href="https://aurora.example/">', out)

    def test_product_page_tags(self):
        out = self._build({"type": "product", "obj": self.product},
                          path="/p/brass-lamp/")
        self.assertIn("<title>Brass Lamp | Aurora</title>", out)
        self.assertIn('href="https://aurora.example/p/brass-lamp/"', out)
        self.assertIn('property="og:type" content="product"', out)
        self.assertIn('"@type":"Product"', out)
        self.assertIn('"price":"2999"', out)
        self.assertIn('twitter:site" content="@aurora"', out)

    def test_breadcrumb_jsonld(self):
        out = self._build({"type": "product", "obj": self.product,
                           "crumbs": [("Home", "/"), ("Brass Lamp", "/p/brass-lamp/")]})
        self.assertIn('"@type":"BreadcrumbList"', out)
        self.assertIn("https://aurora.example/p/brass-lamp/", out)

    def test_noindex_flag(self):
        out = self._build({"type": "page", "noindex": True})
        self.assertIn('name="robots" content="noindex,follow"', out)

    def test_falls_back_to_store_description(self):
        out = self._build({"type": "shop"}, path="/shop/")
        self.assertIn('content="Aurora storefront"', out)


class MetaForPathTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="P", status="active", currency="INR")

    def test_canonical_paths_match_shopfront_routes(self):
        prod = Product.objects.create(project=self.project, title="T", slug="t",
                                      price=Decimal("1"), status="active")
        cat = Category.objects.create(project=self.project, name="Décor", slug="decor")
        self.assertEqual(
            seo_svc.meta_for(self.project, obj=prod, obj_type="product")["canonical"],
            "/p/t/")
        self.assertEqual(
            seo_svc.meta_for(self.project, obj=cat, obj_type="category")["canonical"],
            "/c/decor/")


class SitemapEntriesTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="P", status="active", currency="INR")
        self.p = Product.objects.create(project=self.project, title="T", slug="t",
                                        price=Decimal("1"), status="active")
        Category.objects.create(project=self.project, name="C", slug="c")

    def test_entries_use_real_paths_and_include_home(self):
        locs = [e["loc"] for e in seo_svc.sitemap_entries(self.project)]
        self.assertIn("/", locs)
        self.assertIn("/shop/", locs)
        self.assertIn("/p/t/", locs)
        self.assertIn("/c/c/", locs)
        self.assertNotIn("/product/t/", locs)


@override_settings(ALLOWED_HOSTS=["*"])
class MiddlewareTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Mid", status="active", currency="INR",
                                              primary_domain="mid.example")

    def _run(self, *, path="/app/", seo=None, html=_HTML, htmx=False, ctype="text/html; charset=utf-8"):
        req = RequestFactory().get(path, **({"HTTP_HX_REQUEST": "true"} if htmx else {}))
        req.project = self.project
        req.user = AnonymousUser()
        if seo is not None:
            req._seo = seo

        def get_response(r):
            resp = HttpResponse(html)
            resp["Content-Type"] = ctype
            return resp

        return SeoInjectionMiddleware(get_response)(req)

    def test_replaces_skin_title_and_adds_canonical(self):
        body = self._run(seo={"type": "home"}).content.decode()
        self.assertNotIn("Skin Title", body)
        self.assertNotIn("skin desc", body)
        self.assertIn("<title>Mid</title>", body)
        self.assertIn('<link rel="canonical"', body)
        self.assertIn('application/ld+json', body)

    def test_skips_htmx(self):
        body = self._run(seo={"type": "home"}, htmx=True).content.decode()
        self.assertIn("Skin Title", body)

    def test_skips_non_html(self):
        body = self._run(ctype="application/json").content.decode()
        self.assertIn("Skin Title", body)

    def test_private_path_is_noindexed(self):
        body = self._run(path="/app/checkout/").content.decode()
        self.assertIn("noindex", body)

    def test_no_head_no_change(self):
        body = self._run(html="<html><body>x</body></html>").content.decode()
        self.assertEqual(body, "<html><body>x</body></html>")


@override_settings(ALLOWED_HOSTS=["*"])
class SitemapViewTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="S", status="active", currency="INR",
                                              primary_domain="s.example")
        SeoSettings.objects.create(project=self.project, sitemap_enabled=True)
        for i in range(3):
            Product.objects.create(project=self.project, title=f"P{i}", slug=f"p{i}",
                                   price=Decimal("1"), status="active")

    def test_urlset_for_small_store(self):
        r = self.client.get("/sitemap.xml", HTTP_HOST="s.example")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"<urlset", r.content)
        self.assertIn(b"http://s.example/p/p0/", r.content)

    def test_sitemap_index_when_over_chunk(self):
        with mock.patch("apps.cms.views._SITEMAP_CHUNK", 2):
            r = self.client.get("/sitemap.xml", HTTP_HOST="s.example")
            self.assertIn(b"<sitemapindex", r.content)
            chunk = self.client.get("/sitemap-1.xml", HTTP_HOST="s.example")
            self.assertEqual(chunk.status_code, 200)
            self.assertIn(b"<urlset", chunk.content)

    def test_disabled_sitemap_404(self):
        SeoSettings.objects.filter(project=self.project).update(sitemap_enabled=False)
        r = self.client.get("/sitemap.xml", HTTP_HOST="s.example")
        self.assertEqual(r.status_code, 404)

    def test_robots_has_disallow_and_sitemap(self):
        r = self.client.get("/robots.txt", HTTP_HOST="s.example")
        self.assertIn(b"Disallow: /checkout/", r.content)
        self.assertIn(b"Sitemap: http://s.example/sitemap.xml", r.content)


class IndexNowTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="I", status="active", currency="INR",
                                              primary_domain="i.example")

    @override_settings(SEO_INDEXNOW_KEY="abc123")
    def test_product_save_pings_when_key_set(self):
        with mock.patch("apps.seo.tasks.indexnow_submit.delay") as delay:
            Product.objects.create(project=self.project, title="X", slug="x",
                                   price=Decimal("1"), status="active")
        delay.assert_called_once()
        host, urls = delay.call_args.args
        self.assertEqual(host, "i.example")
        self.assertIn("https://i.example/p/x/", urls)

    def test_no_ping_without_key(self):
        with mock.patch("apps.seo.tasks.indexnow_submit.delay") as delay:
            Product.objects.create(project=self.project, title="X", slug="x2",
                                   price=Decimal("1"), status="active")
        delay.assert_not_called()

    @override_settings(SEO_INDEXNOW_KEY="abc123")
    def test_no_ping_for_draft(self):
        with mock.patch("apps.seo.tasks.indexnow_submit.delay") as delay:
            Product.objects.create(project=self.project, title="X", slug="x3",
                                   price=Decimal("1"), status="draft")
        delay.assert_not_called()
