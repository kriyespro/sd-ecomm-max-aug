"""Home page "Shop by category" tile label stays readable on a narrow phone
screen: padded, centered, smaller on mobile than desktop — default and ornza
overlay the label directly on the tile image, so a long category name needs
room instead of running edge-to-edge at a fixed desktop size."""

from django.test import TestCase

from apps.categories.models import Category
from apps.catalog.models import Product
from apps.cms.models import Skin, ThemeSettings
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Domain, Project


class CategoryTileMobileLabelTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="TileCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="tileco.test", is_verified=True)
        bust_project_chrome(self.project.pk)
        cat = Category.objects.create(
            project=self.project, name="Statement Necklaces & Pendants",
            slug="statement-necklaces", is_active=True,
        )
        Product.objects.create(
            project=self.project, title="Necklace", slug="necklace",
            status="active", price="999", category=cat,
        )

    def _use_skin(self, slug):
        Skin.objects.filter(is_default=True).update(is_default=False)
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"skin": Skin.objects.get(slug=slug)},
        )
        bust_project_chrome(self.project.pk)

    def test_default_skin_tile_label_is_padded_and_responsive(self):
        body = self.client.get("/", HTTP_HOST="tileco.test").content.decode()
        self.assertIn(
            'class="absolute inset-0 flex items-center justify-center '
            'px-2 text-center font-display text-lg leading-tight text-paper sm:text-2xl"',
            body,
        )

    def test_ornza_skin_tile_label_is_padded_and_responsive(self):
        self._use_skin("ornza")
        body = self.client.get("/", HTTP_HOST="tileco.test").content.decode()
        self.assertIn(
            'class="absolute inset-0 flex items-center justify-center '
            'px-2 text-center font-display text-lg leading-tight text-paper sm:text-2xl"',
            body,
        )
