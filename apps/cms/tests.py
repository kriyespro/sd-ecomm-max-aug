import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.cms.models import Banner, StoreProfile
from apps.projects.models import Project


def _png(width=2000, height=500):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (180, 140, 87)).save(buf, format="PNG")
    return buf.getvalue()


class StoreProfileLogoOptimiseTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Shrink", status="active")

    def test_large_logo_is_reencoded_to_webp(self):
        raw = _png()
        sp = StoreProfile(project=self.project)
        sp.logo.save("huge-logo.png", SimpleUploadedFile("huge-logo.png", raw), save=False)
        sp.save()

        self.assertTrue(sp.logo.name.endswith(".sd.webp"))
        self.assertLess(sp.logo.size, len(raw))

    def test_second_save_does_not_reprocess(self):
        sp = StoreProfile(project=self.project)
        sp.logo.save("logo.png", SimpleUploadedFile("logo.png", _png()), save=False)
        sp.save()
        name = sp.logo.name
        sp.tagline = "changed"
        sp.save()
        self.assertEqual(sp.logo.name, name)


class BannerImageOptimiseTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Banners", status="active")

    def test_large_banner_is_reencoded(self):
        raw = _png(2570, 1600)
        b = Banner(project=self.project, name="Hero")
        b.image.save("hero.png", SimpleUploadedFile("hero.png", raw), save=False)
        b.save()
        self.assertTrue(b.image.name.endswith(".sd.webp"))
        self.assertLess(b.image.size, len(raw))


class SkinTailwindHelpersTests(TestCase):
    """Guards for the compiled-per-skin Tailwind wiring (config/jinja2.py)."""

    def test_rgb_channels_conversion(self):
        from config.jinja2 import _rgb_channels

        self.assertEqual(_rgb_channels("#c9a55a"), "201 165 90")
        self.assertEqual(_rgb_channels("#fff"), "255 255 255")
        self.assertEqual(_rgb_channels("111111"), "17 17 17")

    def test_rgb_channels_falls_back_on_bad_input(self):
        from config.jinja2 import _rgb_channels

        for bad in ("", None, "not-a-colour", "#12"):
            self.assertEqual(_rgb_channels(bad), "17 17 17")

    def test_skin_css_href_none_when_not_built(self):
        from config.jinja2 import _compiled_css_href, _skin_css_href

        _compiled_css_href.cache_clear()
        self.assertIsNone(_skin_css_href("definitely-not-a-real-skin"))

    def test_site_css_href_returns_none_or_static_url(self):
        from config.jinja2 import _compiled_css_href, _site_css_href

        _compiled_css_href.cache_clear()
        href = _site_css_href()
        # None until the bundle is built + collected; a static URL afterwards.
        self.assertTrue(href is None or href.endswith("site/site.css"))

    def test_every_built_in_skin_base_still_has_a_tailwind_config_block(self):
        import re
        from pathlib import Path

        from django.conf import settings

        skins = Path(settings.BASE_DIR) / "templates" / "shopfront" / "skins"
        for base in skins.glob("*/base.jinja"):
            text = base.read_text()
            self.assertRegex(
                text, r"tailwind\.config\s*=\s*\{",
                f"{base.parent.name}: build_tailwind_skins.py needs this block",
            )
            self.assertIn("skin_css_href(skin_slug)", text, base.parent.name)
            self.assertRegex(text, r"--accent: \{\{ \(accent or '#[0-9a-fA-F]+'\)")
