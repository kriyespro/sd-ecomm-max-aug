from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.landing_pages import PAGES, attribution_from, signup_href
from apps.projects.models import Domain, Project


@override_settings(ALLOWED_HOSTS=["*"], PLATFORM_HOSTS=["shop.test"])
class AdLandingTests(TestCase):
    def test_every_page_renders_with_one_cta_target(self):
        for slug in PAGES:
            resp = self.client.get(f"/{slug}/", HTTP_HOST="shop.test")
            self.assertEqual(resp.status_code, 200, slug)
            body = resp.content.decode()
            self.assertIn("Start free trial", body)
            self.assertIn(f"lp={slug}", body)
            # Every link out of the page goes to signup (or is the canonical).
            import re
            hrefs = [h for h in re.findall(r'<a [^>]*href="([^"]+)"', body)]
            self.assertTrue(hrefs)
            for h in hrefs:
                self.assertTrue(
                    h.startswith("/accounts/signup/") or h in ("/privacy/", "/terms/"),
                    (slug, h),
                )

    def test_landing_page_sets_no_cookie(self):
        resp = self.client.get("/online-store-builder/", HTTP_HOST="shop.test")
        self.assertEqual(len(resp.cookies), 0)

    def test_attribution_passthrough_on_cta(self):
        resp = self.client.get(
            "/instagram-sellers/?utm_source=meta&utm_campaign=c1&fbclid=abc&junk=x&ref=DGC1",
            HTTP_HOST="shop.test",
        )
        body = resp.content.decode()
        self.assertIn("utm_source=meta", body)
        self.assertIn("fbclid=abc", body)
        self.assertIn("ref=DGC1", body)
        self.assertNotIn("junk=x", body)

    def test_store_host_does_not_serve_it(self):
        project = Project.objects.create(name="Acme")
        Domain.objects.create(project=project, host="shop.acme.test", is_verified=True)
        resp = self.client.get("/fashion-store/", HTTP_HOST="shop.acme.test")
        self.assertEqual(resp.status_code, 404)

    def test_signup_stashes_source_and_ignores_unknown_lp(self):
        self.client.get(
            reverse("accounts:signup") + "?lp=fashion-store&utm_source=google&gclid=g1",
            HTTP_HOST="shop.test",
        )
        self.assertEqual(
            self.client.session["signup_source"],
            {"utm_source": "google", "gclid": "g1", "lp": "fashion-store"},
        )
        self.client.get(reverse("accounts:signup") + "?utm_source=x&lp=evil<script>", HTTP_HOST="shop.test")
        self.assertNotIn("lp", self.client.session["signup_source"])

    def test_helpers_trim_and_whitelist(self):
        from django.http import QueryDict

        q = QueryDict("utm_source=" + "a" * 500 + "&bogus=1")
        out = attribution_from(q)
        self.assertEqual(list(out), ["utm_source"])
        self.assertEqual(len(out["utm_source"]), 200)
        self.assertTrue(signup_href("/accounts/signup/", QueryDict(""), "x").endswith("lp=x"))


@override_settings(ALLOWED_HOSTS=["*"], PLATFORM_HOSTS=["shop.test"],
                   LEGAL_ENTITY_NAME="Acme Pvt Ltd", LEGAL_CONTACT_EMAIL="legal@acme.test")
class LegalPageTests(TestCase):
    def test_privacy_and_terms_render(self):
        for path, needle in (("/privacy/", "Privacy Policy"), ("/terms/", "Terms of Service")):
            resp = self.client.get(path, HTTP_HOST="shop.test")
            self.assertEqual(resp.status_code, 200, path)
            self.assertContains(resp, needle)
            self.assertContains(resp, "Acme Pvt Ltd")
            self.assertContains(resp, "legal@acme.test")
            self.assertEqual(len(resp.cookies), 0)

    def test_landing_and_signup_link_to_legal(self):
        for path in ("/", "/fashion-store/", "/accounts/signup/"):
            resp = self.client.get(path, HTTP_HOST="shop.test")
            self.assertContains(resp, 'href="/privacy/"')
            self.assertContains(resp, 'href="/terms/"')
