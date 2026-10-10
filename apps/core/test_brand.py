import json

from django.test import TestCase, override_settings

from apps.core.brand import brand_for_host, platform_domains
from apps.projects import subdomains
from apps.projects.models import Domain, Project

BRANDS = [{"key": "acme", "name": "Acme", "hosts": ["acme.test"], "base_domain": "acme.test",
           "legal_entity": "Acme Pvt Ltd", "contact_email": "help@acme.test"}]


@override_settings(
    PLATFORM_HOSTS=["main.test", "acme.test"], PLATFORM_BASE_DOMAIN="main.test",
    PLATFORM_BRANDS=BRANDS, LEGAL_ENTITY_NAME="Main",
)
class BrandTests(TestCase):
    def test_host_resolution(self):
        self.assertEqual(brand_for_host("acme.test").key, "acme")
        self.assertEqual(brand_for_host("www.acme.test").key, "acme")
        self.assertEqual(brand_for_host("shop.acme.test").key, "acme")
        self.assertEqual(brand_for_host("main.test").key, "default")
        self.assertEqual(brand_for_host("other.example").key, "default")
        self.assertEqual(platform_domains(), {"main.test", "acme.test"})

    def test_landing_uses_brand(self):
        r = self.client.get("/", HTTP_HOST="acme.test")
        self.assertContains(r, "Acme")
        self.assertNotContains(r, "shopinaday")
        r = self.client.get("/", HTTP_HOST="main.test")
        self.assertContains(r, "Main")

    def test_legal_entity(self):
        r = self.client.get("/terms/", HTTP_HOST="acme.test")
        self.assertContains(r, "Acme Pvt Ltd")

    def test_subdomain_per_brand(self):
        p = Project.objects.create(name="Shop", brand="acme")
        self.assertEqual(subdomains.base_domain(p), "acme.test")
        d = subdomains.assign(p, "shop")
        self.assertEqual(d.host, "shop.acme.test")
        self.assertEqual(subdomains.current_slug(p), "shop")
        q = Project.objects.create(name="Other")
        self.assertEqual(subdomains.assign(q, "shop").host, "shop.main.test")
        # same slug on different apexes does not collide
        self.assertEqual(Domain.objects.filter(host__startswith="shop.").count(), 2)

    def test_brand_subdomain_resolves_store(self):
        p = Project.objects.create(name="Shop", brand="acme", status=Project.Status.ACTIVE)
        subdomains.assign(p, "shop")
        r = self.client.get("/", HTTP_HOST="shop.acme.test")
        self.assertEqual(r.status_code, 200)
