"""Hero banner YouTube-video background renders across every skin that owns
its own home.jinja (the rest fall back to `default` via the skin loader)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.cms.models import Banner, BannerPlacement, Skin
from apps.projects.models import Domain, Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class HeroVideoRenderTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Acme", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="acme.test", is_verified=True)
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")
        self.banner = Banner.objects.create(
            project=self.project, name="Hero", placement=BannerPlacement.HERO,
            heading="Spring drop", video_url="https://youtu.be/dQw4w9WgXcQ",
        )

    def test_youtube_id_extracted_on_save(self):
        self.assertEqual(self.banner.video_youtube_id, "dQw4w9WgXcQ")

    def _render(self, skin_slug):
        skin = Skin.objects.get(slug=skin_slug)
        self.client.force_login(self.admin)
        resp = self.client.get("/", HTTP_HOST="acme.test",
                               data={"preview_skin": skin.pk})
        self.assertEqual(resp.status_code, 200, f"{skin_slug}: {resp.status_code}")
        self.assertContains(resp, "youtube-nocookie.com/embed/dQw4w9WgXcQ")
        return resp

    def test_default_skin(self):
        self._render("default")

    def test_botanica2_skin(self):
        self._render("botanica2")

    def test_botanica3_skin(self):
        self._render("botanica3")

    def test_ornza_skin(self):
        self._render("ornza")

    def test_ornza_skin_plays_on_mobile_too(self):
        # Split hero: a desktop column (lg:block) and a separate mobile
        # background (lg:hidden) — both must carry the video, not just one.
        resp = self._render("ornza")
        self.assertEqual(
            resp.content.decode().count("youtube-nocookie.com/embed/dQw4w9WgXcQ"), 2,
        )
        self.assertContains(resp, 'class="absolute inset-0 z-0 lg:hidden"')

    def test_distinct_mobile_video_on_default_skin(self):
        self.banner.mobile_video_url = "https://youtu.be/oHg5SJYRHA0"
        self.banner.save()
        resp = self._render("default")
        self.assertContains(resp, "youtube-nocookie.com/embed/dQw4w9WgXcQ")  # desktop
        self.assertContains(resp, "youtube-nocookie.com/embed/oHg5SJYRHA0")  # mobile

    def test_distinct_mobile_video_on_ornza_skin(self):
        self.banner.mobile_video_url = "https://youtu.be/oHg5SJYRHA0"
        self.banner.save()
        resp = self._render("ornza")
        self.assertContains(resp, "youtube-nocookie.com/embed/dQw4w9WgXcQ")  # desktop
        self.assertContains(resp, "youtube-nocookie.com/embed/oHg5SJYRHA0")  # mobile

    def test_no_mobile_override_reuses_desktop_video(self):
        # unchanged from before this field existed: one video, both breakpoints
        resp = self._render("default")
        self.assertEqual(
            resp.content.decode().count("youtube-nocookie.com/embed/dQw4w9WgXcQ"), 1,
        )

    def test_no_video_falls_back_to_image(self):
        self.banner.video_url = ""
        self.banner.save()
        self.client.force_login(self.admin)
        skin = Skin.objects.get(slug="default")
        resp = self.client.get("/", HTTP_HOST="acme.test", data={"preview_skin": skin.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "youtube-nocookie.com")
