"""ThemeSettings.hide_featured_eyebrow — botanica2/botanica3 only. The small
"Best sellers" label above the Featured heading is a plain <p>, not one of
the storewide headings, so it stayed left-aligned even when heading_align
was set to center; a merchant can now hide it outright or have it follow
heading_align like every other heading does."""

from django.test import TestCase

from apps.cms.models import Skin, ThemeSettings
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Domain, Project


class FeaturedEyebrowTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="EyebrowCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="eyebrowco.test", is_verified=True)
        bust_project_chrome(self.project.pk)

    def _use_skin(self, slug):
        Skin.objects.filter(is_default=True).update(is_default=False)
        theme, _ = ThemeSettings.objects.get_or_create(project=self.project)
        theme.skin = Skin.objects.get(slug=slug)
        theme.save()
        bust_project_chrome(self.project.pk)

    def _set(self, **kw):
        ThemeSettings.objects.update_or_create(project=self.project, defaults=kw)
        bust_project_chrome(self.project.pk)

    def test_botanica2_eyebrow_shows_left_by_default(self):
        self._use_skin("botanica2")
        body = self.client.get("/", HTTP_HOST="eyebrowco.test").content.decode()
        self.assertIn('<p class="text-left text-[11px]', body)
        self.assertIn(">Best sellers</p>", body)

    def test_botanica2_hide_removes_the_eyebrow(self):
        self._use_skin("botanica2")
        self._set(hide_featured_eyebrow=True)
        body = self.client.get("/", HTTP_HOST="eyebrowco.test").content.decode()
        self.assertNotIn(">Best sellers</p>", body)
        self.assertIn(">Loved by the community</h2>", body)

    def test_botanica2_center_align_moves_the_eyebrow_too(self):
        self._use_skin("botanica2")
        self._set(heading_align="center")
        body = self.client.get("/", HTTP_HOST="eyebrowco.test").content.decode()
        self.assertIn('<p class="text-center text-[11px]', body)

    def test_botanica3_hide_removes_the_eyebrow(self):
        self._use_skin("botanica3")
        self._set(hide_featured_eyebrow=True)
        body = self.client.get("/", HTTP_HOST="eyebrowco.test").content.decode()
        self.assertNotIn(">Best sellers</p>", body)

    def test_botanica3_center_align_moves_the_eyebrow_too(self):
        self._use_skin("botanica3")
        self._set(heading_align="center")
        body = self.client.get("/", HTTP_HOST="eyebrowco.test").content.decode()
        self.assertIn('<p class="text-center text-[11px]', body)

    def test_default_skin_is_unaffected(self):
        """hide_featured_eyebrow is botanica-only — default skin's "Featured"
        heading has no eyebrow at all, so the flag is simply a no-op there."""
        self._set(hide_featured_eyebrow=True)
        body = self.client.get("/", HTTP_HOST="eyebrowco.test").content.decode()
        self.assertIn(">Featured</h2>", body)
