"""ThemeSettings.section_titles — per-skin custom text for every home page
section heading (Shop by category, Featured/Loved by the community, Best
sellers, testimonials, etc). A key absent from it must render that heading's
own hardcoded default text, so an untouched store's headings are unchanged."""

from django.test import TestCase

from apps.cms.models import Skin, ThemeSettings
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Domain, Project


class SectionTitlesTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="TitleCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="titleco.test", is_verified=True)
        bust_project_chrome(self.project.pk)

    def _set(self, titles):
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"section_titles": titles},
        )
        bust_project_chrome(self.project.pk)

    def _use_skin(self, slug):
        Skin.objects.filter(is_default=True).update(is_default=False)
        theme, _ = ThemeSettings.objects.get_or_create(project=self.project)
        theme.skin = Skin.objects.get(slug=slug)
        theme.save()
        bust_project_chrome(self.project.pk)

    def test_untouched_store_keeps_default_heading_text(self):
        body = self.client.get("/", HTTP_HOST="titleco.test").content.decode()
        self.assertIn(">Featured</h2>", body)

    def test_default_skin_custom_titles_render(self):
        self._set({"categories": "Our Collections", "featured": "Bestsellers"})
        body = self.client.get("/", HTTP_HOST="titleco.test").content.decode()
        self.assertIn(">Bestsellers</h2>", body)
        self.assertNotIn(">Featured</h2>", body)

    def test_botanica2_eyebrow_and_heading_both_override(self):
        self._use_skin("botanica2")
        self._set({"featured_eyebrow": "Top rated", "featured": "Customer favourites"})
        body = self.client.get("/", HTTP_HOST="titleco.test").content.decode()
        self.assertIn(">Top rated</p>", body)
        self.assertIn(">Customer favourites</h2>", body)
        self.assertNotIn(">Best sellers</p>", body)
        self.assertNotIn(">Loved by the community</h2>", body)

    def test_ornza_bestsellers_heading_overrides(self):
        self._use_skin("ornza")
        self._set({"bestsellers": "Fan favourites"})
        body = self.client.get("/", HTTP_HOST="titleco.test").content.decode()
        self.assertIn(">Fan favourites</h2>", body)
        self.assertNotIn(">Best sellers</h2>", body)

    def test_botanica3_budget_and_instagram_headings_override(self):
        from apps.cms.models import BudgetBand

        BudgetBand.objects.create(
            project=self.project, label="Under ₹999", is_active=True, order=1,
        )
        self._use_skin("botanica3")
        self._set({"budget_bands": "Pick a price", "instagram": "Follow along"})
        body = self.client.get("/", HTTP_HOST="titleco.test").content.decode()
        self.assertIn(">Pick a price</h2>", body)
        self.assertNotIn(">Shop by budget</h2>", body)
