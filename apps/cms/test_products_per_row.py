"""ThemeSettings.products_per_row (4 or 5) — desktop-only column count for
every product-card grid: home page Featured/New arrivals and the shop "all
products" grid. Mobile/tablet breakpoints are untouched either way."""

from django.core.files.uploadedfile import SimpleUploadedFile
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
        self.assertIn("lg:grid-cols-4", body)
        self.assertNotIn("lg:grid-cols-5", body)

    def test_five_per_row_applies_on_home(self):
        self._set(5)
        body = self.client.get("/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("lg:grid-cols-5", body)

    def test_five_per_row_applies_on_shop_page(self):
        self._set(5)
        body = self.client.get("/shop/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("lg:grid-cols-5", body)

    def test_mobile_and_tablet_breakpoints_are_unaffected(self):
        self._set(5)
        body = self.client.get("/shop/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("grid-cols-2", body)
        self.assertIn("md:grid-cols-3", body)

    def test_category_tile_grid_is_not_affected_by_products_per_row(self):
        """products_per_row only touches product-card grids — the category
        tile grid on the home page keeps its own column count regardless."""
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
        self.assertIn("lg:grid-cols-5", body)

    def test_botanica3_five_per_row(self):
        self._use_skin("botanica3")
        self._set(5)
        body = self.client.get("/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("lg:grid-cols-5", body)

    def test_ornza_five_per_row(self):
        self._use_skin("ornza")
        self._set(5)
        body = self.client.get("/", HTTP_HOST="gridco.test").content.decode()
        self.assertIn("lg:grid-cols-5", body)
