from django.test import RequestFactory, TestCase, override_settings

from apps.core.middleware import _resolve_project
from apps.projects.models import Domain, Project


@override_settings(ALLOWED_HOSTS=["*"])
class RootViewTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Acme")
        Domain.objects.create(
            project=self.project, host="shop.acme.test", is_verified=True
        )

    def test_store_host_redirects_to_storefront(self):
        resp = self.client.get("/", HTTP_HOST="shop.acme.test")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/app/")

    def test_unverified_host_gets_landing(self):
        Domain.objects.create(project=self.project, host="pending.acme.test")
        resp = self.client.get("/", HTTP_HOST="pending.acme.test")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Start your ecommerce store in a day")

    def test_platform_host_gets_landing(self):
        resp = self.client.get("/", HTTP_HOST="www.mnxstore.test")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Start your ecommerce store in a day")
        # Pricing is built from the seeded billing plans, with one highlighted.
        self.assertContains(resp, "Most popular")
        self.assertContains(resp, "Choose Growth")

    @override_settings(PLATFORM_HOSTS=["shop.acme.test"])
    def test_platform_host_overrides_a_matching_store(self):
        # A verified Domain row points here, but the host is the platform's own.
        resp = self.client.get("/", HTTP_HOST="shop.acme.test")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Start your ecommerce store in a day")


@override_settings(ALLOWED_HOSTS=["*"])
class ResolveProjectTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.project = Project.objects.create(name="Acme")
        Domain.objects.create(
            project=self.project, host="shop.acme.test", is_verified=True
        )

    def _req(self, host):
        return self.rf.get("/", HTTP_HOST=host)

    def test_verified_domain_resolves(self):
        self.assertEqual(_resolve_project(self._req("shop.acme.test")), self.project)

    @override_settings(PLATFORM_HOSTS=["shop.acme.test"])
    def test_platform_host_never_resolves(self):
        self.assertIsNone(_resolve_project(self._req("shop.acme.test")))

    @override_settings(PLATFORM_HOSTS=["mnxstore.test"])
    def test_platform_host_matches_after_www_strip(self):
        Domain.objects.create(
            project=self.project, host="mnxstore.test", is_verified=True
        )
        self.assertIsNone(_resolve_project(self._req("www.mnxstore.test")))
