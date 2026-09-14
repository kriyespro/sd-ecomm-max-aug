"""The home page "Shorts" (UGC video reel) heading is centered by design —
same pattern as testimonials/"Why it works" — but still respects an explicit
ThemeSettings.heading_align override, unlike before when it ignored the
storewide align control entirely."""

from django.test import TestCase

from apps.cms.models import ThemeSettings, UGCVideo
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Domain, Project


class ShortsHeadingAlignTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="ShortsCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="shortsco.test", is_verified=True)
        bust_project_chrome(self.project.pk)
        UGCVideo.objects.create(
            project=self.project, youtube_url="https://www.youtube.com/shorts/dQw4w9WgXcQ",
        )

    def test_centered_by_default(self):
        body = self.client.get("/", HTTP_HOST="shortsco.test").content.decode()
        self.assertIn('<h2 class="mb-6 text-center text-2xl font-semibold">Shorts</h2>', body)

    def test_left_align_override_applies(self):
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"heading_align": "left"},
        )
        bust_project_chrome(self.project.pk)
        body = self.client.get("/", HTTP_HOST="shortsco.test").content.decode()
        self.assertIn('<h2 class="mb-6 text-left text-2xl font-semibold">Shorts</h2>', body)

    def test_right_align_override_applies(self):
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"heading_align": "right"},
        )
        bust_project_chrome(self.project.pk)
        body = self.client.get("/", HTTP_HOST="shortsco.test").content.decode()
        self.assertIn('<h2 class="mb-6 text-right text-2xl font-semibold">Shorts</h2>', body)
