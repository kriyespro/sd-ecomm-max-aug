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
