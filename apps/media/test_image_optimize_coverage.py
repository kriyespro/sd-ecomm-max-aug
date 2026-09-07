"""Every image-upload field across the platform (avatar, brand/category
images, skin previews, store/project logos, SEO OG images) must be
auto-optimized on save the same way product images / store logos / banners
already are."""

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.accounts.models import Profile
from apps.catalog.models import Brand
from apps.categories.models import Category
from apps.cms.models import Skin
from apps.projects.models import Project
from apps.seo.models import SeoMeta, SeoSettings

User = get_user_model()


def _png(width=2000, height=500):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (180, 140, 87)).save(buf, format="PNG")
    return buf.getvalue()


class ImageOptimizeCoverageTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Optimize Co", status="active")

    def test_profile_avatar_is_reencoded(self):
        user = User.objects.create_user("u1", "u1@t.test", "pw")
        profile = user.profile
        raw = _png()
        profile.avatar.save("avatar.png", SimpleUploadedFile("avatar.png", raw), save=False)
        profile.save()
        self.assertTrue(profile.avatar.name.endswith(".sd.webp"))
        self.assertLess(profile.avatar.size, len(raw))

    def test_brand_logo_is_reencoded(self):
        raw = _png()
        brand = Brand(project=self.project, name="Acme")
        brand.logo.save("logo.png", SimpleUploadedFile("logo.png", raw), save=False)
        brand.save()
        self.assertTrue(brand.logo.name.endswith(".sd.webp"))
        self.assertLess(brand.logo.size, len(raw))

    def test_category_images_are_reencoded(self):
        raw = _png(2000, 1000)
        cat = Category(project=self.project, name="Shoes")
        cat.image.save("img.png", SimpleUploadedFile("img.png", raw), save=False)
        cat.banner.save("banner.png", SimpleUploadedFile("banner.png", raw), save=False)
        cat.icon.save("icon.png", SimpleUploadedFile("icon.png", _png(400, 400)), save=False)
        cat.save()
        self.assertTrue(cat.image.name.endswith(".sd.webp"))
        self.assertTrue(cat.banner.name.endswith(".sd.webp"))
        self.assertTrue(cat.icon.name.endswith(".sd.webp"))

    def test_skin_preview_image_is_reencoded(self):
        raw = _png()
        skin = Skin(slug="opt-test", label="Opt Test")
        skin.preview_image.save("preview.png", SimpleUploadedFile("preview.png", raw), save=False)
        skin.save()
        self.assertTrue(skin.preview_image.name.endswith(".sd.webp"))
        self.assertLess(skin.preview_image.size, len(raw))

    def test_project_logo_is_reencoded_favicon_untouched(self):
        raw = _png()
        self.project.logo.save("logo.png", SimpleUploadedFile("logo.png", raw), save=False)
        self.project.favicon.save("fav.png", SimpleUploadedFile("fav.png", raw), save=False)
        self.project.save()
        self.assertTrue(self.project.logo.name.endswith(".sd.webp"))
        self.assertFalse(self.project.favicon.name.endswith(".sd.webp"))
        self.assertEqual(self.project.favicon.size, len(raw))

    def test_seo_og_images_are_reencoded(self):
        raw = _png(1600, 900)
        settings_row = SeoSettings(project=self.project)
        settings_row.default_og_image.save("og.png", SimpleUploadedFile("og.png", raw), save=False)
        settings_row.save()
        self.assertTrue(settings_row.default_og_image.name.endswith(".sd.webp"))

        meta = SeoMeta(project=self.project, path="/promo/")
        meta.og_image.save("og2.png", SimpleUploadedFile("og2.png", raw), save=False)
        meta.save()
        self.assertTrue(meta.og_image.name.endswith(".sd.webp"))
