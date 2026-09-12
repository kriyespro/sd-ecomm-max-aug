"""Promo/category/product banner placements must display a widescreen upload
(1600x760, the platform's documented banner size) uncropped. They used to be
fixed-pixel-height boxes (h-28, h-40, h-[280px]...) with object-cover, wildly
mismatched against 1600x760 (~2.1:1) and cropping the top/bottom hard —
independent of the actual uploaded image, `object-cover` fills a box shaped
nothing like the source. Category tiles and product cards never had this bug
because they already use a proportional aspect-[W/H] container matching their
own recommended upload ratio; banners now do the same (aspect-[40/19], the
simplified form of 1600:760)."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.categories.models import Category
from apps.cms.models import Banner, BannerPlacement, Skin, ThemeSettings
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Domain, Project


def _png(size=(1600, 760)):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, "blue").save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


class BannerAspectRatioTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="BannerCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="banner.test", is_verified=True)
        self.category = Category.objects.create(
            project=self.project, name="Rings", slug="rings", is_active=True,
        )

    def _banner(self, placement, **kw):
        b = Banner.objects.create(
            project=self.project, placement=placement, name=placement,
            heading=f"{placement} heading", **kw,
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

    def test_default_skin_promo_banner_uses_matching_aspect_ratio(self):
        self._banner(BannerPlacement.PROMO)
        resp = self.client.get("/", HTTP_HOST="banner.test")
        self.assertContains(resp, "aspect-[40/19]")
        self.assertNotIn("h-[280px]", resp.content.decode())

    def test_default_skin_category_banner_uses_matching_aspect_ratio(self):
        self._banner(BannerPlacement.CATEGORY, category=self.category)
        resp = self.client.get("/shop/?category=rings", HTTP_HOST="banner.test")
        self.assertContains(resp, "aspect-[40/19]")
        self.assertNotIn("h-40 w-full", resp.content.decode())

    def test_default_skin_product_banner_uses_matching_aspect_ratio(self):
        from apps.catalog.models import Product

        self._banner(BannerPlacement.PRODUCT)
        Product.objects.create(
            project=self.project, title="Ring", slug="ring", status="active", price="999",
        )
        resp = self.client.get("/p/ring/", HTTP_HOST="banner.test")
        self.assertContains(resp, "aspect-[40/19]")
        self.assertNotIn("h-28 w-full", resp.content.decode())

    def test_botanica2_promo_and_category_banners_use_matching_aspect_ratio(self):
        self._use_skin("botanica2")
        self._banner(BannerPlacement.PROMO)
        self._banner(BannerPlacement.CATEGORY, category=self.category)
        home = self.client.get("/", HTTP_HOST="banner.test")
        self.assertContains(home, "aspect-[40/19]")
        shop = self.client.get("/shop/?category=rings", HTTP_HOST="banner.test")
        self.assertContains(shop, "aspect-[40/19]")

    def test_botanica3_promo_and_category_banners_use_matching_aspect_ratio(self):
        self._use_skin("botanica3")
        self._banner(BannerPlacement.PROMO)
        self._banner(BannerPlacement.CATEGORY, category=self.category)
        home = self.client.get("/", HTTP_HOST="banner.test")
        self.assertContains(home, "aspect-[40/19]")
        shop = self.client.get("/shop/?category=rings", HTTP_HOST="banner.test")
        self.assertContains(shop, "aspect-[40/19]")

    def test_ornza_skin_promo_banner_uses_matching_aspect_ratio(self):
        self._use_skin("ornza")
        self._banner(BannerPlacement.PROMO)
        home = self.client.get("/", HTTP_HOST="banner.test")
        self.assertContains(home, "aspect-[40/19]")
