import io
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.catalog.models import Product, ProductImage
from apps.categories.models import Category
from apps.cms.models import Banner, Skin, ThemeSettings
from apps.core.store_resolver import bust_project_chrome, store_chrome
from apps.projects.models import Domain, Project


def _png(color="green"):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


def _banner(project, **kw):
    kw.setdefault("name", "b")
    kw.setdefault("placement", "hero")
    b = Banner(project=project, image=SimpleUploadedFile("b.png", _png()), **kw)
    b.save()
    return b


class HeroSlidesChromeTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Slide", status="active")

    def test_all_live_hero_banners_become_slides(self):
        _banner(self.project, name="one", priority=1)
        _banner(self.project, name="two", priority=2)
        _banner(self.project, name="off", priority=3, is_active=False)
        bust_project_chrome(self.project.pk)
        c = store_chrome(self.project)
        self.assertEqual([b.name for b in c["hero_slides"]], ["one", "two"])
        self.assertEqual(c["hero_banner"].name, "one")  # first, for other skins

    def test_category_above_hero_flag_flows_through(self):
        ThemeSettings.objects.create(project=self.project, category_above_hero=True)
        bust_project_chrome(self.project.pk)
        self.assertTrue(store_chrome(self.project)["category_above_hero"])


@override_settings(ALLOWED_HOSTS=["*"])
class Botanica3LayoutRenderTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="B3L", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="b3l.test", is_verified=True)
        ThemeSettings.objects.create(
            project=self.project, skin=Skin.objects.get(slug="botanica3"),
        )
        cat = Category.objects.create(project=self.project, name="Oils", is_active=True)
        p = Product.objects.create(project=self.project, title="Oil", category=cat,
                                   status="active", search_indexed=True, price=Decimal("399"))
        ProductImage.objects.create(product=p, image=SimpleUploadedFile("a.png", _png()))
        ProductImage.objects.create(product=p, image=SimpleUploadedFile("b.png", _png("red")))
        self.project.refresh_from_db()

    def _get(self):
        bust_project_chrome(self.project.pk)
        return self.client.get("/", HTTP_HOST="b3l.test")

    def test_card_has_hover_second_image_and_wishlist(self):
        r = self._get()
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("group-hover:opacity-100", body)   # 2nd image fade-in
        self.assertIn("group-hover:opacity-0", body)      # 1st image fade-out
        self.assertIn("/wishlist/toggle/", body)          # heart toggle on the card

    def test_hero_slider_controls_appear_with_two_banners(self):
        _banner(self.project, name="h1", priority=1, heading="First hero")
        _banner(self.project, name="h2", priority=2, heading="Second hero")
        body = self._get().content.decode()
        self.assertIn("First hero", body)
        self.assertIn("Second hero", body)
        self.assertIn('aria-label="Next"', body)

    def test_text_hidden_hero_drops_its_copy(self):
        _banner(self.project, name="silent", priority=1, heading="SHOULD NOT SHOW",
                subheading="hidden too", text_hidden=True)
        body = self._get().content.decode()
        self.assertNotIn("SHOULD NOT SHOW", body)
        self.assertNotIn("hidden too", body)

    def test_category_above_hero_renders_two_category_sections(self):
        body = self._get().content.decode()
        self.assertEqual(body.count("Shop by category"), 1)

        ThemeSettings.objects.filter(project=self.project).update(category_above_hero=True)
        body = self._get().content.decode()
        self.assertIn("Browse the shop", body)
        self.assertEqual(body.count("Oils"), body.count("Oils"))  # both rows list it
        self.assertGreaterEqual(body.count(">Oils<"), 2)

    def test_second_promo_banner_moves_below_instagram(self):
        _banner(self.project, name="p1", placement="promo", priority=1, heading="PROMO ONE")
        _banner(self.project, name="p2", placement="promo", priority=2, heading="PROMO TWO")
        body = self._get().content.decode()
        self.assertLess(body.index("PROMO ONE"), body.index("Best sellers"))
        self.assertGreater(body.index("PROMO TWO"), body.index("Best sellers"))
