"""ThemeSettings.heading_align/heading_size/heading_font — one storewide
control for every section heading (Shop by category, New arrivals,
testimonials, "Why it works", etc), NOT merchant banner text (hero/promo/
category/product), which already has text_hidden/hide_cta/hide_overlay.

Defaults (blank align, "md" size, "display" font) must reproduce each
heading's own original look exactly — some headings (testimonials, "Why it
works") are center-aligned by the skin's own design, not by any single
default this feature could otherwise safely pick.
"""

from django.test import TestCase

from apps.categories.models import Category
from apps.cms.models import Skin, ThemeSettings
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Domain, Project


class HeadingStyleDefaultsTests(TestCase):
    """No ThemeSettings row at all — the common case for a fresh store."""

    def setUp(self):
        self.project = Project.objects.create(
            name="HeadingDefaultCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="headingdefault.test", is_verified=True)

    def test_left_by_default_headings_stay_left(self):
        body = self.client.get("/", HTTP_HOST="headingdefault.test").content.decode()
        self.assertIn('font-display text-left text-3xl">Featured</h2>', body)

    def test_default_heading_link_row_stays_justify_between(self):
        """"Featured" shares its row with a "View all" link — untouched
        stores must keep the link pinned to the far end, unchanged."""
        body = self.client.get("/", HTTP_HOST="headingdefault.test").content.decode()
        self.assertIn('<div class="mb-8 flex items-end justify-between">', body)

    def test_center_by_design_headings_stay_centered(self):
        from apps.catalog.models import Product
        from apps.reviews.models import Review, ReviewStatus

        p = Product.objects.create(
            project=self.project, title="Ring", slug="ring", status="active", price="999",
        )
        Review.objects.create(
            project=self.project, product=p, author_name="A", author_email="a@t.test",
            rating=5, body="Lovely", status=ReviewStatus.APPROVED,
        )
        body = self.client.get("/", HTTP_HOST="headingdefault.test").content.decode()
        self.assertIn('font-display text-center text-3xl">What people say</h2>', body)


class HeadingStyleOverrideTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="HeadingOverrideCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="headingoverride.test", is_verified=True)

    def _set_theme(self, **kw):
        ThemeSettings.objects.update_or_create(project=self.project, defaults=kw)
        bust_project_chrome(self.project.pk)

    def _use_skin(self, slug):
        Skin.objects.filter(is_default=True).update(is_default=False)
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"skin": Skin.objects.get(slug=slug)},
        )
        bust_project_chrome(self.project.pk)

    def test_center_align_applies_to_a_normally_left_heading(self):
        self._set_theme(heading_align="center")
        body = self.client.get("/", HTTP_HOST="headingoverride.test").content.decode()
        self.assertIn('font-display text-center text-3xl">Featured</h2>', body)

    def test_right_align_applies(self):
        self._set_theme(heading_align="right")
        body = self.client.get("/", HTTP_HOST="headingoverride.test").content.decode()
        self.assertIn('font-display text-right text-3xl">Featured</h2>', body)

    def test_center_align_also_moves_the_heading_link_row(self):
        """"Featured" sits in a flex row next to "View all" — as a flex item
        it shrinks to its own text width, so text-center on the <h2> alone
        has nothing to visibly center against. The row's own justify-* must
        follow heading_align too, or centering silently does nothing."""
        self._set_theme(heading_align="center")
        body = self.client.get("/", HTTP_HOST="headingoverride.test").content.decode()
        self.assertIn('<div class="mb-8 flex items-end justify-center">', body)

    def test_right_align_also_moves_the_heading_link_row(self):
        self._set_theme(heading_align="right")
        body = self.client.get("/", HTTP_HOST="headingoverride.test").content.decode()
        self.assertIn('<div class="mb-8 flex items-end justify-end">', body)

    def test_size_override_replaces_the_heading_own_default_size(self):
        self._set_theme(heading_size="lg")
        body = self.client.get("/", HTTP_HOST="headingoverride.test").content.decode()
        self.assertIn('font-display text-left text-3xl sm:text-4xl">Featured</h2>', body)
        self.assertNotIn('font-display text-left text-3xl">Featured</h2>', body)

    def test_font_override_switches_to_sans(self):
        self._set_theme(heading_font="sans")
        body = self.client.get("/", HTTP_HOST="headingoverride.test").content.decode()
        self.assertIn('font-sans text-left text-3xl">Featured</h2>', body)

    def test_override_also_reaches_centered_by_design_headings(self):
        """An explicit non-default choice wins even over a skin's own
        center-alignment design intent for that heading."""
        from apps.catalog.models import Product
        from apps.reviews.models import Review, ReviewStatus

        p = Product.objects.create(
            project=self.project, title="Ring", slug="ring", status="active", price="999",
        )
        Review.objects.create(
            project=self.project, product=p, author_name="A", author_email="a@t.test",
            rating=5, body="Lovely", status=ReviewStatus.APPROVED,
        )
        self._set_theme(heading_align="right")
        body = self.client.get("/", HTTP_HOST="headingoverride.test").content.decode()
        self.assertIn('font-display text-right text-3xl">What people say</h2>', body)

    def test_botanica2_category_heading_respects_override(self):
        from apps.catalog.models import Product

        cat = Category.objects.create(
            project=self.project, name="Rings", slug="rings", is_active=True,
        )
        Product.objects.create(
            project=self.project, title="Ring", slug="ring", status="active",
            price="999", category=cat,
        )
        self._use_skin("botanica2")
        self._set_theme(heading_align="center")
        body = self.client.get("/", HTTP_HOST="headingoverride.test").content.decode()
        self.assertIn('font-display text-center text-2xl sm:text-3xl">Shop by category</h2>', body)
        self.assertIn(
            '<div class="mb-9 flex flex-wrap items-end justify-center gap-x-4 gap-y-1">', body,
        )
