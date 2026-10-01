from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.control.store_qr import custom_domain_url
from apps.projects.models import Domain, Project

PNG = b"\x89PNG\r\n\x1a\n"


@override_settings(PLATFORM_BASE_DOMAIN="shopinaday.com", PLATFORM_HOSTS=["shopinaday.com"])
class StoreQrTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Sharma Jewellers", status="active", feature_flags={"onboarded": True}
        )
        self.owner = get_user_model().objects.create_user(
            username="qro", email="qro@t.test", password="pw", is_staff=True
        )
        Membership.objects.create(user=self.owner, project=self.project, role="owner")
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def _domain(self, host, verified=True, primary=False):
        return Domain.objects.create(
            project=self.project, host=host, is_verified=verified, is_primary=primary
        )

    def test_platform_subdomain_only_gives_no_qr(self):
        self._domain("sharma.shopinaday.com", primary=True)
        self.assertIsNone(custom_domain_url(Project.objects.get(pk=self.project.pk)))
        self.assertEqual(self.client.get("/admin/store-qr.png").status_code, 404)
        resp = self.client.get("/admin/")
        self.assertContains(resp, "connect a domain")
        self.assertNotContains(resp, "Download PNG")

    def test_unverified_custom_domain_gives_no_qr(self):
        self._domain("sharmajewellers.in", verified=False)
        self.assertEqual(self.client.get("/admin/store-qr.png").status_code, 404)

    def test_custom_domain_beats_subdomain(self):
        self._domain("sharma.shopinaday.com", primary=True)
        self._domain("sharmajewellers.in")
        self.assertEqual(
            custom_domain_url(Project.objects.get(pk=self.project.pk)),
            "https://sharmajewellers.in/",
        )

    def test_png_and_download(self):
        self._domain("sharmajewellers.in", primary=True)
        resp = self.client.get("/admin/store-qr.png")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")
        self.assertTrue(resp.content.startswith(PNG))
        self.assertNotIn("attachment", resp.get("Content-Disposition", ""))
        dl = self.client.get("/admin/store-qr.png?download=1")
        self.assertIn('filename="sharma-jewellers-qr.png"', dl["Content-Disposition"])
        page = self.client.get("/admin/")
        self.assertContains(page, "Download PNG")
        self.assertContains(page, "sharmajewellers.in")

    def test_requires_login(self):
        self._domain("sharmajewellers.in", primary=True)
        self.client.logout()
        self.assertNotEqual(self.client.get("/admin/store-qr.png").status_code, 200)
