"""Shorts and the Instagram feed already scrolled horizontally on manual
drag/swipe — neither auto-advanced. Both now use auto_scroll_attrs()
(the same macro built for the category-row overflow case)."""

from django.test import TestCase

from apps.cms.models import InstagramItem, Skin, ThemeSettings, UGCVideo
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Domain, Project


class ShortsAutoScrollTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="ScrollCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="scrollco.test", is_verified=True)
        UGCVideo.objects.create(
            project=self.project, youtube_url="https://www.youtube.com/shorts/dQw4w9WgXcQ",
        )
        bust_project_chrome(self.project.pk)

    def test_shorts_row_auto_scrolls(self):
        body = self.client.get("/", HTTP_HOST="scrollco.test").content.decode()
        idx = body.index(">Shorts<")
        window = body[idx:idx + 1500]
        self.assertIn("setInterval", window)
        self.assertIn("scrollBy", window)


class InstagramAutoScrollTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="ScrollCo2", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="scrollco2.test", is_verified=True)
        Skin.objects.filter(is_default=True).update(is_default=False)
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"skin": Skin.objects.get(slug="botanica3")},
        )
        for i in range(8):
            InstagramItem.objects.create(project=self.project, caption=f"post {i}", order=i, is_active=True)
        bust_project_chrome(self.project.pk)

    def test_instagram_row_auto_scrolls_and_shows_more_than_six(self):
        body = self.client.get("/", HTTP_HOST="scrollco2.test").content.decode()
        idx = body.index("From our Instagram")
        window = body[idx:idx + 1500]
        self.assertIn("setInterval", window)
        self.assertIn("scrollBy", window)
        self.assertEqual(body.count('alt="post'), 8)
