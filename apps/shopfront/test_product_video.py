"""Optional per-product YouTube video (Product.video_url, mirroring
Banner.video_url) renders on the PDP — only on skins that own their own
product.jinja (the rest fall back to `default` via the skin loader)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.catalog.models import Product
from apps.cms.models import Skin
from apps.projects.models import Domain, Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ProductVideoRenderTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Acme", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="acme.test", is_verified=True)
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")
        self.product = Product.objects.create(
            project=self.project, title="Ring", slug="ring", status="active", price="999",
            video_url="https://youtu.be/dQw4w9WgXcQ",
        )

    def test_youtube_id_extracted_on_save(self):
        self.assertEqual(self.product.video_youtube_id, "dQw4w9WgXcQ")

    def _render(self, skin_slug):
        skin = Skin.objects.get(slug=skin_slug)
        self.client.force_login(self.admin)
        resp = self.client.get(
            "/p/ring/", HTTP_HOST="acme.test", data={"preview_skin": skin.pk},
        )
        self.assertEqual(resp.status_code, 200, f"{skin_slug}: {resp.status_code}")
        return resp

    def test_default_skin_shows_video(self):
        resp = self._render("default")
        self.assertContains(resp, "youtube-nocookie.com/embed/dQw4w9WgXcQ")

    def test_zyvira_skin_shows_video(self):
        resp = self._render("zyvira")
        self.assertContains(resp, "youtube-nocookie.com/embed/dQw4w9WgXcQ")

    def test_blank_video_renders_nothing(self):
        self.product.video_url = ""
        self.product.save()
        resp = self._render("default")
        self.assertNotContains(resp, "youtube-nocookie.com")
