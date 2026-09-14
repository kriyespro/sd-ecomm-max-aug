"""ThemeSettings.category_mobile_slider — optional, off by default. When on,
"Shop by category" becomes a swipeable single-row slider below the tablet
breakpoint on every skin; desktop/tablet always stay a wrapping grid either
way."""

from django.test import TestCase

from apps.categories.models import Category
from apps.catalog.models import Product
from apps.cms.models import Skin, ThemeSettings
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Domain, Project


class CategoryMobileSliderTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="CatSliderCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="catslider.test", is_verified=True)
        bust_project_chrome(self.project.pk)
        cat = Category.objects.create(
            project=self.project, name="Rings", slug="rings", is_active=True,
        )
        Product.objects.create(
            project=self.project, title="Ring", slug="ring", status="active",
            price="999", category=cat,
        )

    def _use_skin(self, slug):
        Skin.objects.filter(is_default=True).update(is_default=False)
        theme, _ = ThemeSettings.objects.get_or_create(project=self.project)
        theme.skin = Skin.objects.get(slug=slug)
        theme.save()
        bust_project_chrome(self.project.pk)

    def _enable(self):
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"category_mobile_slider": True},
        )
        bust_project_chrome(self.project.pk)

    def test_off_by_default_keeps_the_wrapping_grid(self):
        # "snap-mandatory" also appears in the always-on product rail slider
        # (a separate feature) — check the category-specific classes instead.
        body = self.client.get("/", HTTP_HOST="catslider.test").content.decode()
        self.assertIn('class="grid grid-cols-2 gap-4 md:grid-cols-3">', body)
        self.assertNotIn("w-[45%]", body)

    def test_default_skin_slider_markup_when_enabled(self):
        self._enable()
        body = self.client.get("/", HTTP_HOST="catslider.test").content.decode()
        self.assertIn("snap-x snap-mandatory", body)
        self.assertIn("w-[45%] shrink-0 snap-start", body)

    def test_ornza_slider_markup_when_enabled(self):
        self._use_skin("ornza")
        self._enable()
        body = self.client.get("/", HTTP_HOST="catslider.test").content.decode()
        self.assertIn("snap-x snap-mandatory", body)
        self.assertIn("w-[45%] shrink-0 snap-start", body)

    def test_botanica2_slider_markup_when_enabled(self):
        self._use_skin("botanica2")
        self._enable()
        body = self.client.get("/", HTTP_HOST="catslider.test").content.decode()
        self.assertIn("snap-x snap-mandatory", body)
        self.assertIn("w-[30%] shrink-0 snap-start", body)

    def test_botanica3_slider_markup_when_enabled(self):
        self._use_skin("botanica3")
        self._enable()
        body = self.client.get("/", HTTP_HOST="catslider.test").content.decode()
        self.assertIn("snap-x snap-mandatory", body)
        self.assertIn("w-[30%] shrink-0 snap-start", body)

    def test_botanica3_off_keeps_budget_band_grid_untouched(self):
        """category_mobile_slider only touches the category tile grid — the
        separate budget-band grid (a different section) keeps its own
        markup regardless."""
        from apps.cms.models import BudgetBand

        BudgetBand.objects.create(
            project=self.project, label="Under ₹999", is_active=True, order=1,
        )
        self._use_skin("botanica3")
        self._enable()
        body = self.client.get("/", HTTP_HOST="catslider.test").content.decode()
        self.assertIn(
            'class="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-5">', body,
        )
