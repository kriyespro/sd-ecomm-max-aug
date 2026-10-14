"""Zyvira skin renders home, shop and a product with its own base/home/
product/card — a fine-jewellery storefront skin (deep wine + gold)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.cms.models import Skin
from apps.projects.models import Domain, Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ZyviraSkinTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Acme", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="acme.test", is_verified=True)
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")
        self.client.force_login(self.admin)
        self.skin = Skin.objects.get(slug="zyvira")

    def _get(self, path):
        return self.client.get(path, HTTP_HOST="acme.test",
                               data={"preview_skin": self.skin.pk})

    def test_home_uses_zyvira_templates(self):
        resp = self._get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "DM Serif Display")
        self.assertContains(resp, "Kumbh Sans")
        self.assertContains(resp, "zy-rule")

    def test_shop_renders(self):
        resp = self._get("/shop/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "tracking-luxe")

    def test_card_renders_with_sale_badge(self):
        from apps.catalog.models import Product
        from apps.categories.models import Category

        cat = Category.objects.create(project=self.project, name="Rings", slug="rings")
        Product.objects.create(
            project=self.project, category=cat, title="Gold Ring", slug="gold-ring",
            price=Decimal("1000"), sale_price=Decimal("750"), status="active",
        )
        resp = self._get("/shop/")
        self.assertContains(resp, "Add to bag")
        self.assertContains(resp, "Sale")

    def test_product_page_renders(self):
        from apps.catalog.models import Product
        from apps.categories.models import Category

        cat = Category.objects.create(project=self.project, name="Rings", slug="rings")
        p = Product.objects.create(
            project=self.project, category=cat, title="Gold Ring", slug="gold-ring",
            price=Decimal("1000"), status="active",
        )
        resp = self._get(f"/p/{p.slug}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Add to bag")
        self.assertContains(resp, "Care instructions")
