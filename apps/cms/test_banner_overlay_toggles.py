"""Every Banner placement gets three independent per-banner toggles:
`text_hidden` (heading/subheading), `hide_cta` (the button), `hide_overlay`
(the dark tint/gradient behind the text) — any combination, including all
three at once for a pure uncropped image with nothing on top.

`text_hidden=True` used to hide the button too (one flag controlled the
whole text+button block). The migration backfill (0018) sets `hide_cta=True`
on every existing banner that had `text_hidden=True`, so old banners keep
looking exactly as they did."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.categories.models import Category
from apps.cms.models import Banner, BannerPlacement, Skin, ThemeSettings
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Domain, Project


def _png(size=(1600, 400)):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, "blue").save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


class BannerOverlayToggleTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="ToggleCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="toggle.test", is_verified=True)
        self.category = Category.objects.create(
            project=self.project, name="Rings", slug="rings", is_active=True,
        )

    def _banner(self, placement, **kw):
        b = Banner.objects.create(
            project=self.project, placement=placement, name=placement,
            heading="Big Sale", subheading="This week only", cta_label="Shop now",
            cta_url="/shop/", **kw,
        )
        b.image.save("banner.png", SimpleUploadedFile("banner.png", _png()), save=True)
        bust_project_chrome(self.project.pk)
        return b

    def _use_skin(self, slug):
        Skin.objects.filter(is_default=True).update(is_default=False)
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"skin": Skin.objects.get(slug=slug)},
        )
        bust_project_chrome(self.project.pk)

    # ---- hero (default skin) --------------------------------------

    def test_hero_default_all_visible(self):
        self._banner(BannerPlacement.HERO)
        body = self.client.get("/", HTTP_HOST="toggle.test").content.decode()
        self.assertIn("Big Sale", body)
        self.assertIn("This week only", body)
        self.assertIn("Shop now", body)
        self.assertIn("bg-gradient-to-t from-ink/85", body)

    def test_hero_hide_overlay_keeps_text(self):
        self._banner(BannerPlacement.HERO, hide_overlay=True)
        body = self.client.get("/", HTTP_HOST="toggle.test").content.decode()
        self.assertNotIn("bg-gradient-to-t from-ink/85", body)
        self.assertIn("Big Sale", body)
        self.assertIn("Shop now", body)

    def test_hero_text_hidden_keeps_button_and_overlay(self):
        self._banner(BannerPlacement.HERO, text_hidden=True)
        body = self.client.get("/", HTTP_HOST="toggle.test").content.decode()
        self.assertNotIn("Big Sale", body)
        self.assertNotIn("This week only", body)
        self.assertIn("Shop now", body)
        self.assertIn("bg-gradient-to-t from-ink/85", body)

    def test_hero_hide_cta_keeps_text_and_overlay(self):
        self._banner(BannerPlacement.HERO, hide_cta=True)
        body = self.client.get("/", HTTP_HOST="toggle.test").content.decode()
        self.assertIn("Big Sale", body)
        self.assertNotIn("Shop now", body)
        self.assertIn("bg-gradient-to-t from-ink/85", body)

    def test_hero_all_three_hidden_is_image_only(self):
        self._banner(BannerPlacement.HERO, text_hidden=True, hide_cta=True, hide_overlay=True)
        body = self.client.get("/", HTTP_HOST="toggle.test").content.decode()
        self.assertNotIn("Big Sale", body)
        self.assertNotIn("Shop now", body)
        self.assertNotIn("bg-gradient-to-t from-ink/85", body)

    # ---- promo (default skin) --------------------------------------

    def test_promo_hide_overlay_keeps_text_and_button(self):
        self._banner(BannerPlacement.PROMO, hide_overlay=True)
        body = self.client.get("/", HTTP_HOST="toggle.test").content.decode()
        # "bg-ink/30" alone also matches the page's mobile-nav backdrop —
        # count occurrences rather than a bare assertNotIn.
        self.assertEqual(body.count("bg-ink/30"), 1)  # the mobile-menu backdrop only
        self.assertIn("Big Sale", body)
        self.assertIn("Shop now", body)

    def test_promo_hide_cta_only(self):
        self._banner(BannerPlacement.PROMO, hide_cta=True)
        body = self.client.get("/", HTTP_HOST="toggle.test").content.decode()
        self.assertIn("Big Sale", body)
        self.assertNotIn("Shop now", body)
        self.assertIn("bg-ink/30", body)

    # ---- category (default skin) ------------------------------------

    def test_category_hide_overlay_keeps_text(self):
        self._banner(BannerPlacement.CATEGORY, category=self.category, hide_overlay=True)
        body = self.client.get("/shop/?category=rings", HTTP_HOST="toggle.test").content.decode()
        self.assertNotIn("bg-gradient-to-t from-ink/75", body)
        self.assertIn("Big Sale", body)

    def test_category_text_hidden(self):
        self._banner(BannerPlacement.CATEGORY, category=self.category, text_hidden=True)
        body = self.client.get("/shop/?category=rings", HTTP_HOST="toggle.test").content.decode()
        self.assertNotIn("Big Sale", body)
        self.assertIn("bg-gradient-to-t from-ink/75", body)

    def test_category_text_hidden_also_clears_the_alt_attribute(self):
        """text_hidden means no text associated with the banner at all —
        including for screen readers, not just the visible heading/subheading."""
        self._banner(BannerPlacement.CATEGORY, category=self.category, text_hidden=True)
        body = self.client.get("/shop/?category=rings", HTTP_HOST="toggle.test").content.decode()
        self.assertNotIn('alt="Big Sale"', body)

    # ---- product (default skin, shared product.jinja) ---------------

    def test_product_hide_cta_only(self):
        from apps.catalog.models import Product

        self._banner(BannerPlacement.PRODUCT, hide_cta=True)
        Product.objects.create(
            project=self.project, title="Ring", slug="ring", status="active", price="999",
        )
        body = self.client.get("/p/ring/", HTTP_HOST="toggle.test").content.decode()
        self.assertIn("Big Sale", body)
        self.assertNotIn("Shop now", body)

    # ---- popup (default skin) — no overlay concept, text+button only ---

    def test_popup_text_hidden_keeps_button(self):
        self._banner(BannerPlacement.POPUP, text_hidden=True)
        body = self.client.get("/", HTTP_HOST="toggle.test").content.decode()
        self.assertNotIn("Big Sale", body)
        self.assertNotIn('alt="Big Sale"', body)
        self.assertIn("Shop now", body)

    # ---- botanica3 slider hero ---------------------------------------

    def test_botanica3_hero_slider_hide_overlay(self):
        self._use_skin("botanica3")
        self._banner(BannerPlacement.HERO, hide_overlay=True)
        body = self.client.get("/", HTTP_HOST="toggle.test").content.decode()
        self.assertNotIn("bg-gradient-to-t from-ink/85", body)
        self.assertIn("Big Sale", body)

    def test_botanica3_hero_slider_hide_cta_keeps_text(self):
        self._use_skin("botanica3")
        self._banner(BannerPlacement.HERO, hide_cta=True)
        body = self.client.get("/", HTTP_HOST="toggle.test").content.decode()
        self.assertIn("Big Sale", body)
        self.assertNotIn("Shop now", body)

    # ---- botanica2 hero -------------------------------------------

    def test_botanica2_hero_hide_overlay(self):
        self._use_skin("botanica2")
        self._banner(BannerPlacement.HERO, hide_overlay=True)
        body = self.client.get("/", HTTP_HOST="toggle.test").content.decode()
        self.assertNotIn("bg-gradient-to-t from-ink/85", body)
        self.assertIn("Big Sale", body)

    # ---- ornza promo -------------------------------------------------

    def test_ornza_promo_hide_overlay(self):
        self._use_skin("ornza")
        self._banner(BannerPlacement.PROMO, hide_overlay=True)
        body = self.client.get("/", HTTP_HOST="toggle.test").content.decode()
        self.assertNotIn("bg-[#080604]/40", body)
        self.assertIn("Big Sale", body)
