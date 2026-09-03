from django.test import TestCase, override_settings

from apps.projects import subdomains
from apps.projects.models import Domain, Project

BASE = "shopinaday.test"


@override_settings(PLATFORM_BASE_DOMAIN=BASE)
class SubdomainHelperTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Rack", status="active")

    def test_slugify(self):
        self.assertEqual(subdomains.slugify("  Ada's  Shop!! "), "ada-s-shop")
        self.assertEqual(subdomains.slugify("A_B_C"), "a-b-c")

    def test_unique_slug_avoids_reserved_and_collisions(self):
        self.assertEqual(subdomains.unique_slug("www"), "www-store")

        Domain.objects.create(project=self.project, host=f"nova.{BASE}")
        other = Project.objects.create(name="N2", status="active")
        self.assertEqual(subdomains.unique_slug("nova", project=other), "nova-2")

    def test_assign_creates_verified_primary_and_replaces_old(self):
        d1 = subdomains.assign(self.project, "rack")
        self.assertTrue(d1.is_verified)
        self.assertTrue(d1.is_primary)
        self.project.refresh_from_db()
        self.assertEqual(self.project.primary_domain, f"rack.{BASE}")

        d2 = subdomains.assign(self.project, "rackhouse")
        self.assertFalse(Domain.objects.filter(host=f"rack.{BASE}").exists())
        self.assertEqual(subdomains.current_slug(self.project), "rackhouse")
        self.project.refresh_from_db()
        self.assertEqual(self.project.primary_domain, f"rackhouse.{BASE}")
        self.assertEqual(d2.host, f"rackhouse.{BASE}")

    def test_is_available(self):
        self.assertTrue(subdomains.is_available("freshname"))
        self.assertFalse(subdomains.is_available("admin"))
        self.assertFalse(subdomains.is_available("x"))  # too short
        subdomains.assign(self.project, "taken")
        self.assertFalse(subdomains.is_available("taken"))
        self.assertTrue(subdomains.is_available("taken", project=self.project))

    @override_settings(PLATFORM_BASE_DOMAIN="")
    def test_no_base_domain_is_a_noop(self):
        self.assertIsNone(subdomains.assign(self.project, "rack"))
        self.assertEqual(subdomains.current_slug(self.project), "")
