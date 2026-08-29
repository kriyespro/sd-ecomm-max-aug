from django.test import TestCase, override_settings

from apps.projects.models import Domain, Project


@override_settings(ALLOWED_HOSTS=["*"])
class RootViewTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Acme")
        Domain.objects.create(
            project=self.project, host="shop.acme.test", is_verified=True
        )

    def test_known_host_redirects_to_storefront(self):
        resp = self.client.get("/", HTTP_HOST="shop.acme.test")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/app/")

    def test_unverified_host_404s(self):
        Domain.objects.create(project=self.project, host="pending.acme.test")
        resp = self.client.get("/", HTTP_HOST="pending.acme.test")
        self.assertEqual(resp.status_code, 404)

    def test_unknown_host_404s(self):
        resp = self.client.get("/", HTTP_HOST="nope.example.test")
        self.assertEqual(resp.status_code, 404)
