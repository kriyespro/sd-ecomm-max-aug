from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.catalog.models import Product
from apps.cms.models import StoreProfile
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class OnboardingGateTests(TestCase):
    def setUp(self):
        # A brand-new store — no "onboarded" flag.
        self.project = Project.objects.create(name="FreshCo", status="active")
        self.owner = User.objects.create_user(
            username="own", email="own@fresh.test", password="pw", is_staff=True
        )
        Membership.objects.create(user=self.owner, project=self.project, role="owner")
        self._select_store()

    def _select_store(self):
        self.client.force_login(self.owner)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def test_owner_is_redirected_into_the_wizard(self):
        resp = self.client.get("/admin/products/")
        self.assertRedirects(resp, "/admin/start/", fetch_redirect_response=False)

    def test_wizard_itself_is_reachable(self):
        resp = self.client.get("/admin/start/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Set up your store")

    def test_platform_staff_not_gated(self):
        su = User.objects.create_superuser("root", "r@t.test", "pw")
        self.client.force_login(su)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()
        resp = self.client.get("/admin/products/")
        self.assertEqual(resp.status_code, 200)

    def test_finishing_sets_flags_and_profile(self):
        resp = self.client.post(
            "/admin/start/",
            {
                "contact_email": "hi@fresh.test",
                "contact_phone": "+919812345678",
                "address": "1 MG Road, Pune",
                "vertical": "clothing",
            },
        )
        self.assertRedirects(resp, "/admin/products/", fetch_redirect_response=False)

        self.project.refresh_from_db()
        self.assertTrue(self.project.feature_flags["onboarded"])
        self.assertEqual(self.project.feature_flags["vertical"], "clothing")

        prof = StoreProfile.objects.get(project=self.project)
        self.assertEqual(prof.support_email, "hi@fresh.test")
        self.assertEqual(prof.whatsapp, "+919812345678")

        # gate now lets the owner through
        self.assertEqual(self.client.get("/admin/products/").status_code, 200)

    @override_settings(PLATFORM_BASE_DOMAIN="shopinaday.test")
    def test_subdomain_field_prefilled_and_editable(self):
        from apps.projects import subdomains
        from apps.projects.models import Domain

        subdomains.assign(self.project, "freshco")
        resp = self.client.get("/admin/start/")
        self.assertContains(resp, "shopinaday.test")
        self.assertContains(resp, 'value="freshco"')

        resp = self.client.post(
            "/admin/start/",
            {
                "contact_email": "hi@fresh.test", "vertical": "fmcg",
                "subdomain": "fresh-market",
            },
        )
        self.assertRedirects(resp, "/admin/products/", fetch_redirect_response=False)
        self.assertTrue(Domain.objects.filter(
            project=self.project, host="fresh-market.shopinaday.test",
            is_verified=True, is_primary=True,
        ).exists())
        self.assertFalse(Domain.objects.filter(host="freshco.shopinaday.test").exists())

    def test_skip(self):
        resp = self.client.post("/admin/start/skip/")
        self.assertRedirects(resp, "/admin/products/", fetch_redirect_response=False)
        self.project.refresh_from_db()
        self.assertTrue(self.project.feature_flags["onboarded"])
        self.assertEqual(self.project.feature_flags["vertical"], "")


@override_settings(ALLOWED_HOSTS=["*"])
class ProductFormSizeColorTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Wear", status="active",
            feature_flags={"onboarded": True, "vertical": "clothing"},
        )
        self.user = User.objects.create_superuser("root", "r@t.test", "pw")
        self.client.force_login(self.user)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def test_new_product_page_shows_builder(self):
        resp = self.client.get("/admin/products/new/")
        self.assertContains(resp, "Size &amp; colour")

    def test_builder_hidden_for_non_apparel(self):
        self.project.feature_flags = {"onboarded": True, "vertical": "fmcg"}
        self.project.save(update_fields=["feature_flags"])
        resp = self.client.get("/admin/products/new/")
        self.assertNotContains(resp, "Size &amp; colour")

    def test_save_builds_variants(self):
        resp = self.client.post(
            "/admin/products/new/",
            {
                "title": "Cotton Tee", "slug": "", "kind": "simple",
                "price": "799", "status": "active", "sku": "",
                "short_description": "", "description": "",
                "sizes": "S, M, L", "colors": "Black, White",
                "combo_stock[S|||Black]": "5",
                "combo_price[L|||White]": "899",
            },
        )
        self.assertEqual(resp.status_code, 302)
        product = Product.objects.get(project=self.project, title="Cotton Tee")
        self.assertEqual(product.variants.filter(is_active=True).count(), 6)
        self.assertEqual(product.variants.get(name="S / Black").stock, 5)
        self.assertEqual(product.variants.get(name="L / White").price, Decimal("899"))
