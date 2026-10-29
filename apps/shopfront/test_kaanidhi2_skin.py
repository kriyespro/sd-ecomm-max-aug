"""KAANIDHI2.0 renders home, shop, product and the rest of its own full page
set — built from scratch, not a thin override of skins/default/."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.cms.models import Skin
from apps.projects.models import Domain, Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class Kaanidhi2SkinTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Acme", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="acme.test", is_verified=True)
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")
        self.client.force_login(self.admin)
        self.skin = Skin.objects.get(slug="kaanidhi2-0")

    def _get(self, path):
        return self.client.get(path, HTTP_HOST="acme.test", data={"preview_skin": self.skin.pk})

    def test_home_uses_kaanidhi2_templates(self):
        resp = self._get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Jost")
        self.assertContains(resp, "Parisienne")
        self.assertContains(resp, "v2-rule")

    def test_home_category_tile_renders(self):
        from apps.catalog.models import Product
        from apps.categories.models import Category

        cat = Category.objects.create(project=self.project, name="Rings", slug="rings")
        Product.objects.create(
            project=self.project, category=cat, title="Gold Ring", slug="gold-ring",
            price=Decimal("1000"), status="active",
        )
        resp = self._get("/")
        self.assertContains(resp, "Rings")

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
        self.assertContains(resp, "Add to Bag")
        self.assertContains(resp, "Sale 25%")

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

    def test_cart_account_wishlist_track_render(self):
        for path in ("/cart/", "/account/", "/wishlist/", "/track/"):
            resp = self._get(path)
            self.assertEqual(resp.status_code, 200, path)

    def test_checkout_renders_with_item_in_cart(self):
        from apps.catalog.models import Product
        from apps.categories.models import Category

        cat = Category.objects.create(project=self.project, name="Rings", slug="rings")
        p = Product.objects.create(
            project=self.project, category=cat, title="Gold Ring", slug="gold-ring",
            price=Decimal("1000"), status="active",
        )
        self.client.post(
            f"/cart/add/?preview_skin={self.skin.pk}", {"product": p.slug, "quantity": 1},
            HTTP_HOST="acme.test",
        )
        resp = self._get("/checkout/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Place Order")

    def test_wishlist_toggle_echoes_back_style(self):
        from apps.catalog.models import Product
        from apps.categories.models import Category

        cat = Category.objects.create(project=self.project, name="Rings", slug="rings")
        p = Product.objects.create(
            project=self.project, category=cat, title="Gold Ring", slug="gold-ring",
            price=Decimal("1000"), status="active",
        )
        resp = self.client.post(
            f"/wishlist/toggle/?preview_skin={self.skin.pk}",
            {"product": p.slug, "style": "pill"},
            HTTP_HOST="acme.test",
        )
        self.assertContains(resp, 'value="pill"')
        self.assertContains(resp, "Saved")

    def test_card_shows_sold_out_when_stock_is_zero(self):
        from apps.catalog.models import Product
        from apps.categories.models import Category
        from apps.inventory.models import InventoryItem, Warehouse

        cat = Category.objects.create(project=self.project, name="Rings", slug="rings")
        sold_out = Product.objects.create(
            project=self.project, category=cat, title="Empty Ring", slug="empty-ring",
            price=Decimal("1000"), status="active",
        )
        in_stock = Product.objects.create(
            project=self.project, category=cat, title="Stocked Ring", slug="stocked-ring",
            price=Decimal("1000"), status="active",
        )
        wh = Warehouse.objects.create(project=self.project, name="Main", is_default=True)
        InventoryItem.objects.create(warehouse=wh, product=sold_out, quantity=0, reserved=0)
        InventoryItem.objects.create(warehouse=wh, product=in_stock, quantity=5, reserved=0)
        untracked = Product.objects.create(
            project=self.project, category=cat, title="Untracked Ring", slug="untracked-ring",
            price=Decimal("1000"), status="active",
        )
        resp = self._get("/shop/")
        self.assertContains(resp, "Sold out")
        self.assertContains(resp, "Sold Out")
        self.assertContains(resp, "Add to Bag", count=2)

    def test_faq_renders_on_home_when_active(self):
        from apps.cms.models import FAQ

        FAQ.objects.create(project=self.project, group="General", question="Do you ship internationally?", answer="<p>Yes.</p>", order=0, is_active=True)
        resp = self._get("/")
        self.assertContains(resp, "Do you ship internationally?")
