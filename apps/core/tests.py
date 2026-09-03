from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse

from apps.core.middleware import (
    StorefrontHostMiddleware,
    _resolve_project,
)
from apps.projects.models import Domain, Project


@override_settings(ALLOWED_HOSTS=["*"])
class RootViewTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Acme")
        Domain.objects.create(
            project=self.project, host="shop.acme.test", is_verified=True
        )

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
        self.project = Project.objects.create(
            name="Acme", feature_flags={"onboarded": True}
        )
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


@override_settings(ALLOWED_HOSTS=["*"])
class StorefrontHostMiddlewareTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.project = Project.objects.create(
            name="Acme", feature_flags={"onboarded": True}
        )
        Domain.objects.create(
            project=self.project, host="shop.acme.test", is_verified=True
        )

    def _run(self, host):
        request = self.rf.get("/", HTTP_HOST=host)
        StorefrontHostMiddleware(lambda r: r)(request)
        return request

    def test_verified_domain_host_swaps_urlconf(self):
        request = self._run("shop.acme.test")
        self.assertTrue(request.storefront_host)
        self.assertEqual(request.urlconf, "config.storefront_urls")

    def test_primary_domain_host_swaps_urlconf(self):
        self.project.primary_domain = "acme-primary.test"
        self.project.save(update_fields=["primary_domain", "updated_at"])
        request = self._run("acme-primary.test")
        self.assertTrue(request.storefront_host)

    def test_unverified_domain_host_does_not_swap(self):
        Domain.objects.create(project=self.project, host="pending.test")
        request = self._run("pending.test")
        self.assertFalse(request.storefront_host)
        self.assertFalse(hasattr(request, "urlconf"))

    @override_settings(PLATFORM_HOSTS=["shop.acme.test"])
    def test_platform_host_never_swaps(self):
        request = self._run("shop.acme.test")
        self.assertFalse(request.storefront_host)

    def test_store_host_serves_storefront_at_root(self):
        resp = self.client.get("/", HTTP_HOST="shop.acme.test")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("Location", resp)

    def test_store_host_redirects_legacy_app_path(self):
        resp = self.client.get("/app/shop/", HTTP_HOST="shop.acme.test")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/shop/")

    def test_mission_control_reachable_on_store_domain(self):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Membership

        user = get_user_model().objects.create_user(
            username="own", email="own@acme.test", password="pw", is_staff=True
        )
        Membership.objects.create(user=user, project=self.project, role="owner")
        self.client.force_login(user)
        resp = self.client.get("/admin/", HTTP_HOST="shop.acme.test", follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Products")  # store nav rendered


class StorefrontUrlconfTests(TestCase):
    def test_storefront_home_is_at_root(self):
        self.assertEqual(reverse("shopfront:home", urlconf="config.storefront_urls"), "/")

    def test_root_resolves_to_shopfront_home(self):
        match = resolve("/", urlconf="config.storefront_urls")
        self.assertEqual(match.func.view_class.__name__, "HomeView")

    def test_legacy_app_path_redirects_to_root(self):
        match = resolve("/app/shop/", urlconf="config.storefront_urls")
        self.assertEqual(match.func.view_class.__name__, "RedirectView")

    def test_default_urlconf_still_serves_app_prefix(self):
        self.assertEqual(reverse("shopfront:home"), "/app/")


@override_settings(ALLOWED_HOSTS=["*"])
class StoreResolverCacheTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.project = Project.objects.create(name="Acme")

    def test_binding_cached_then_busted_on_verify(self):
        from apps.core.store_resolver import binding_for_host

        d = Domain.objects.create(
            project=self.project, host="cache.acme.test", is_verified=False
        )
        self.assertEqual(binding_for_host("cache.acme.test"), (None, False))
        # verifying the domain must invalidate the negative cache entry
        d.is_verified = True
        d.save(update_fields=["is_verified", "updated_at"])
        self.assertEqual(
            binding_for_host("cache.acme.test"), (self.project.pk, True)
        )

    def test_chrome_cached_then_busted_on_category_change(self):
        from apps.categories.models import Category
        from apps.core.store_resolver import store_chrome

        self.assertEqual(store_chrome(self.project)["categories"], [])
        Category.objects.create(project=self.project, name="Hats", is_active=True)
        names = [c.name for c in store_chrome(self.project)["categories"]]
        self.assertEqual(names, ["Hats"])

    def test_chrome_exposes_store_profile_and_busts_on_save(self):
        from apps.cms.models import StoreProfile
        from apps.core.store_resolver import store_chrome

        self.assertIsNone(store_chrome(self.project)["profile"])
        StoreProfile.objects.create(project=self.project, tagline="Made well")
        chrome = store_chrome(self.project)
        self.assertEqual(chrome["profile"].tagline, "Made well")
        self.assertEqual(chrome["store_logo"], "")


@override_settings(ALLOWED_HOSTS=["*"])
class PartnerPageTests(TestCase):
    def test_page_renders_with_commission_band_and_gate(self):
        resp = self.client.get(reverse("partners"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "20")
        self.assertContains(resp, "30")
        self.assertContains(resp, "3 successful sales")

    def test_valid_application_is_stored(self):
        from apps.accounts.models import PartnerApplication

        resp = self.client.post(reverse("partners"), {
            "full_name": "Ada Ref", "email": "ADA@x.test",
            "audience": "Community of 500 sellers.", "website": "",
        })
        self.assertEqual(resp.status_code, 302)
        app = PartnerApplication.objects.get()
        self.assertEqual(app.email, "ada@x.test")
        self.assertEqual(app.status, "pending")

    def test_honeypot_blocks_submission(self):
        from apps.accounts.models import PartnerApplication

        resp = self.client.post(reverse("partners"), {
            "full_name": "Bot", "email": "bot@x.test",
            "audience": "spam", "website": "http://spam",
        })
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(PartnerApplication.objects.exists())
