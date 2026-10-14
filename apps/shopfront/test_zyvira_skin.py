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

    def test_home_category_tile_renders(self):
        from apps.catalog.models import Product
        from apps.categories.models import Category

        cat = Category.objects.create(project=self.project, name="Rings", slug="rings")
        Product.objects.create(
            project=self.project, category=cat, title="Gold Ring", slug="gold-ring",
            price=Decimal("1000"), status="active",
        )
        resp = self._get("/")
        self.assertContains(resp, "Shop Now")
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

    def test_header_is_transparent_overlay_only_on_home(self):
        resp = self._get("/")
        self.assertContains(resp, "home: true")
        resp2 = self._get("/shop/")
        self.assertContains(resp2, "home: false")

    def test_wishlist_toggle_echoes_back_style(self):
        from apps.catalog.models import Product
        from apps.categories.models import Category

        cat = Category.objects.create(project=self.project, name="Rings", slug="rings")
        p = Product.objects.create(
            project=self.project, category=cat, title="Gold Ring", slug="gold-ring",
            price=Decimal("1000"), status="active",
        )
        # Stay logged in as self.admin (a platform admin, already logged in by
        # setUp) rather than switching users — preview_skin only takes effect
        # for a platform admin, and any authenticated user can wishlist.
        resp = self.client.post(
            f"/wishlist/toggle/?preview_skin={self.skin.pk}",
            {"product": p.slug, "style": "pill"},
            HTTP_HOST="acme.test",
        )
        self.assertContains(resp, 'value="pill"')
        self.assertContains(resp, "Saved")

    def test_shop_category_intro_renders_banner_and_description(self):
        from apps.categories.models import Category

        Category.objects.create(
            project=self.project, name="Rings", slug="rings",
            description="Statement and stacking rings for every day.",
        )
        resp = self.client.get(
            "/shop/", {"category": "rings", "preview_skin": self.skin.pk},
            HTTP_HOST="acme.test",
        )
        self.assertContains(resp, "Statement and stacking rings for every day.")
        self.assertContains(resp, "Acme Collection")

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
        # in_stock has inventory tracked (and available); a third product
        # with NO InventoryItem row at all should also read as available
        # (untracked = always in stock, same convention as the PDP).
        untracked = Product.objects.create(
            project=self.project, category=cat, title="Untracked Ring", slug="untracked-ring",
            price=Decimal("1000"), status="active",
        )
        resp = self._get("/shop/")
        self.assertContains(resp, "Sold out")
        self.assertContains(resp, "Sold Out")
        self.assertContains(resp, "Add to Bag", count=2)  # in_stock + untracked

    def test_card_shows_hover_second_photo(self):
        from apps.catalog.models import Product
        from apps.catalog.models import ProductImage
        from apps.categories.models import Category

        cat = Category.objects.create(project=self.project, name="Rings", slug="rings")
        p = Product.objects.create(
            project=self.project, category=cat, title="Gold Ring", slug="gold-ring",
            price=Decimal("1000"), status="active",
        )
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        import io

        def _png():
            buf = io.BytesIO()
            Image.new("RGB", (600, 600), (200, 150, 90)).save(buf, format="PNG")
            return buf.getvalue()

        for _ in range(2):
            ProductImage.objects.create(
                product=p, image=SimpleUploadedFile("p.png", _png(), content_type="image/png"),
            )
        resp = self._get("/shop/")
        self.assertContains(resp, "group-hover:opacity-100")
