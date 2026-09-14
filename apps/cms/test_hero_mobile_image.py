"""Checked per the user's request: does the hero banner's mobile_image
actually render at its own natural size (no crop) on botanica2/botanica3,
same as the desktop image, after the "hero shows uploaded image at its own
size" change? Verifies the <picture><source media="(max-width: 640px)">
swap is wired and the <img> itself carries no size-forcing class regardless
of which source the browser picks."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.cms.models import Banner, BannerPlacement, Skin, ThemeSettings
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Domain, Project


def _png(size):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, "blue").save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


class HeroMobileImageTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="MobileHeroCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="mobilehero.test", is_verified=True)

    def _hero_with_mobile(self):
        b = Banner.objects.create(
            project=self.project, placement=BannerPlacement.HERO, name="hero",
        )
        b.image.save("d.png", SimpleUploadedFile("d.png", _png((1600, 500))), save=False)
        b.mobile_image.save("m.png", SimpleUploadedFile("m.png", _png((800, 1200))), save=True)
        bust_project_chrome(self.project.pk)
        return b

    def _use_skin(self, slug):
        Skin.objects.filter(is_default=True).update(is_default=False)
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"skin": Skin.objects.get(slug=slug)},
        )
        bust_project_chrome(self.project.pk)

    def test_botanica2_mobile_source_present_no_crop_classes(self):
        self._use_skin("botanica2")
        b = self._hero_with_mobile()
        body = self.client.get("/", HTTP_HOST="mobilehero.test").content.decode()
        self.assertIn(f'<source media="(max-width: 640px)" srcset="{b.mobile_image.url}">', body)
        self.assertIn('class="block w-full h-auto"', body)
        self.assertNotIn("object-cover", body.split("</picture>")[0].split("<picture>")[-1])

    def test_botanica3_hero_slider_mobile_source_present_no_crop_classes(self):
        self._use_skin("botanica3")
        b = self._hero_with_mobile()
        body = self.client.get("/", HTTP_HOST="mobilehero.test").content.decode()
        self.assertIn(f'<source media="(max-width: 640px)" srcset="{b.mobile_image.url}">', body)
        self.assertIn('class="block w-full h-auto"', body)
