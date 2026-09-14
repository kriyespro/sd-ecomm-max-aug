"""ThemeSettings.products_per_row (3/4/5/6) — desktop-only visible-at-once
count for every home page product rail (Featured/New arrivals/Best sellers,
now single-row sliders) and the shop "all products" grid (still a real
wrapping grid). Mobile/tablet breakpoints are untouched either way."""

from django.test import TestCase

from apps.catalog.models import Product
from apps.cms.models import Skin, ThemeSettings
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Domain, Project


class ProductsPerRowTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="GridCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="gridco.test", is_verified=True)
        bust_project_chrome(self.project.pk)
        Product.objects.create(
            project=self.project, title="Ring", slug="ring", status="active", price="999",
        )

    def _set(self, n):
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"products_per_row": n},
        )
        bust_project_chrome(self.project.pk)

    def _use_skin(self, slug):
        Skin.objects.filter(is_default=True).update(is_default=False)
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"skin": Skin.objects.get(slug=slug)},
        )
        bust_project_chrome(self.project.pk)

    def test_default_is_four_per_row_on_home(self):
        body = self.client.get("/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("lg:w-1/4", body)
        self.assertNotIn("lg:w-1/5", body)

    def test_five_per_row_applies_on_home(self):
        self._set(5)
        body = self.client.get("/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("lg:w-1/5", body)

    def test_three_and_six_per_row_are_valid_choices(self):
        self._set(3)
        body = self.client.get("/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("lg:w-1/3", body)

        self._set(6)
        body = self.client.get("/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("lg:w-1/6", body)

    def test_five_per_row_applies_on_shop_page(self):
        self._set(5)
        body = self.client.get("/shop/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("lg:grid-cols-5", body)

    def test_three_and_six_per_row_apply_on_shop_page(self):
        self._set(3)
        body = self.client.get("/shop/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("lg:grid-cols-3", body)

        self._set(6)
        body = self.client.get("/shop/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("lg:grid-cols-6", body)

    def test_mobile_and_tablet_breakpoints_are_unaffected_on_shop_page(self):
        self._set(5)
        body = self.client.get("/shop/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("grid-cols-2", body)
        self.assertIn("md:grid-cols-3", body)

    def test_home_rail_mobile_and_tablet_widths_are_unaffected(self):
        self._set(6)
        body = self.client.get("/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("w-1/2 flex-none snap-start md:w-1/3", body)

    def test_category_tile_grid_is_not_affected_by_products_per_row(self):
        """products_per_row only touches product rails — the category tile
        grid on the home page keeps its own column count regardless."""
        from apps.categories.models import Category

        cat = Category.objects.create(
            project=self.project, name="Rings", slug="rings", is_active=True,
        )
        Product.objects.filter(project=self.project, slug="ring").update(category=cat)
        self._set(5)
        body = self.client.get("/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn('class="grid grid-cols-2 gap-4 md:grid-cols-3">', body)

    def test_botanica2_five_per_row(self):
        self._use_skin("botanica2")
        self._set(5)
        body = self.client.get("/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("lg:w-1/5", body)

    def test_botanica3_five_per_row(self):
        self._use_skin("botanica3")
        self._set(5)
        body = self.client.get("/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("lg:w-1/5", body)

    def test_ornza_five_per_row(self):
        self._use_skin("ornza")
        self._set(5)
        body = self.client.get("/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("lg:w-1/5", body)


class ProductsShownTests(TestCase):
    """ThemeSettings.products_shown (5/8/10/15/20) — total products loaded
    into each home page rail; the view slices to this before it ever reaches
    the slider partial."""

    def setUp(self):
        self.project = Project.objects.create(
            name="ShownCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="showco.test", is_verified=True)
        bust_project_chrome(self.project.pk)
        for i in range(12):
            Product.objects.create(
                project=self.project, title=f"Ring {i}", slug=f"ring-{i}",
                status="active", price="999", is_featured=True,
            )

    def _set(self, n):
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"products_shown": n},
        )
        bust_project_chrome(self.project.pk)

    # Every product here is_featured=True and none is_new_arrival, so the
    # New arrivals rail's own (empty) query falls back to the latest N
    # products too — both rails end up showing `n` cards each.

    def test_default_shows_eight(self):
        body = self.client.get("/", HTTP_HOST="showco.test").content.decode()
        self.assertEqual(body.count('hx-post="/cart/add/"'), 16)

    def test_raising_the_count_loads_more(self):
        self._set(10)
        body = self.client.get("/", HTTP_HOST="showco.test").content.decode()
        self.assertEqual(body.count('hx-post="/cart/add/"'), 20)

    def test_lowering_the_count_loads_fewer(self):
        self._set(5)
        body = self.client.get("/", HTTP_HOST="showco.test").content.decode()
        self.assertEqual(body.count('hx-post="/cart/add/"'), 10)

    def test_slider_arrows_appear_only_when_more_than_one_row(self):
        # products_shown(20 capped to 12 available) > products_per_row(4) default
        self._set(20)
        body = self.client.get("/", HTTP_HOST="showco.test").content.decode()
        self.assertIn('aria-label="Next"', body)

    def test_slider_arrows_absent_when_everything_fits_in_one_row(self):
        self._set(5)
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"products_per_row": 6},
        )
        bust_project_chrome(self.project.pk)
        body = self.client.get("/", HTTP_HOST="showco.test").content.decode()
        self.assertNotIn('aria-label="Next"', body)
