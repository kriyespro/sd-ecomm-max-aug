"""Festive skin renders home, shop and a product with its own base/home/card."""

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.cms.models import Skin
from apps.projects.models import Domain, Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class FestiveSkinTests(TestCase):
    def setUp(self):
        call_command("seed_festive_skin", verbosity=0)
        self.project = Project.objects.create(
            name="Acme", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="acme.test", is_verified=True)
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")
        self.client.force_login(self.admin)
        self.skin = Skin.objects.get(slug="festive")

    def _get(self, path):
        return self.client.get(path, HTTP_HOST="acme.test",
                               data={"preview_skin": self.skin.pk})

    def test_home_uses_festive_templates(self):
        resp = self._get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "fv-gold")
        self.assertContains(resp, "fv-marquee")
        self.assertContains(resp, "Playfair Display")

    def test_shop_renders(self):
        resp = self._get("/shop/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "fv-gold")

    def test_card_renders_with_discount_badge(self):
        from decimal import Decimal

        from apps.catalog.models import Product
        from apps.categories.models import Category

        cat = Category.objects.create(project=self.project, name="Rings", slug="rings")
        Product.objects.create(
            project=self.project, category=cat, title="Gold Ring", slug="gold-ring",
            price=Decimal("1000"), sale_price=Decimal("750"), status="active",
        )
        resp = self._get("/shop/")
        self.assertContains(resp, "Add to bag")
        self.assertContains(resp, "25% off")

    def test_story_section_on_home(self):
        self.assertContains(self._get("/"), "Our promise")
