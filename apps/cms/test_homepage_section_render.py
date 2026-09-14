"""Custom ThemeSettings.homepage_sections orders actually change what order
sections render in — the whole point of the reorder feature. Default (empty)
order must render identically to before this existed (covered by the rest of
the storefront test suite already passing unchanged)."""

from django.test import TestCase

from apps.cms.models import Skin, ThemeSettings
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Domain, Project


class HomepageSectionOrderRenderTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="SectionOrderCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="sectionorder.test", is_verified=True)
        # A fresh test DB reuses pks across test methods; the chrome cache
        # doesn't get cleared between them, so a stale entry from a previous
        # test's project (same pk) could otherwise leak in here.
        bust_project_chrome(self.project.pk)

    def _set_order(self, order):
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"homepage_sections": order},
        )
        bust_project_chrome(self.project.pk)

    def _use_skin(self, slug):
        Skin.objects.filter(is_default=True).update(is_default=False)
        ThemeSettings.objects.update_or_create(
            project=self.project, defaults={"skin": Skin.objects.get(slug=slug)},
        )
        bust_project_chrome(self.project.pk)

    def test_default_skin_custom_order_moves_testimonials_before_featured(self):
        from apps.catalog.models import Product
        from apps.reviews.models import Review, ReviewStatus

        p = Product.objects.create(
            project=self.project, title="Ring", slug="ring", status="active", price="999",
        )
        Review.objects.create(
            project=self.project, product=p, author_name="A", author_email="a@t.test",
            rating=5, body="Lovely", status=ReviewStatus.APPROVED,
        )
        self._set_order(["testimonials", "promo", "categories", "featured", "new_arrivals"])
        body = self.client.get("/", HTTP_HOST="sectionorder.test").content.decode()
        self.assertLess(body.index("What people say"), body.index(">Featured<"))

    def test_default_skin_default_order_has_featured_before_testimonials(self):
        from apps.catalog.models import Product
        from apps.reviews.models import Review, ReviewStatus

        p = Product.objects.create(
            project=self.project, title="Ring", slug="ring", status="active", price="999",
        )
        Review.objects.create(
            project=self.project, product=p, author_name="A", author_email="a@t.test",
            rating=5, body="Lovely", status=ReviewStatus.APPROVED,
        )
        body = self.client.get("/", HTTP_HOST="sectionorder.test").content.decode()
        self.assertLess(body.index(">Featured<"), body.index("What people say"))

    def _category_with_product(self):
        from apps.catalog.models import Product
        from apps.categories.models import Category

        cat = Category.objects.create(
            project=self.project, name="Rings", slug="rings", is_active=True,
        )
        Product.objects.create(
            project=self.project, title="Ring", slug="ring", status="active",
            price="999", category=cat,
        )

    def test_botanica2_custom_order_moves_newsletter_before_categories(self):
        self._category_with_product()
        self._use_skin("botanica2")
        self._set_order(["newsletter", "categories", "promo", "featured", "benefits", "new_arrivals", "testimonials"])
        body = self.client.get("/", HTTP_HOST="sectionorder.test").content.decode()
        self.assertLess(body.index("Get 10% off your first order"), body.index("Shop by category"))

    def test_ornza_custom_order_moves_new_arrivals_before_categories(self):
        from apps.catalog.models import Product

        self._category_with_product()
        Product.objects.create(
            project=self.project, title="Necklace", slug="necklace", status="active",
            price="999", is_new_arrival=True,
        )
        self._use_skin("ornza")
        self._set_order(["new_arrivals", "categories"])
        body = self.client.get("/", HTTP_HOST="sectionorder.test").content.decode()
        self.assertLess(body.index("New arrivals"), body.index("Shop by category"))

    def test_ornza_best_sellers_and_testimonials_are_not_reorderable(self):
        """Only categories/new_arrivals are in ornza's catalogue — best_sellers
        isn't a valid key, so it's silently ignored, not an error."""
        self._use_skin("ornza")
        self._set_order(["best_sellers", "new_arrivals", "categories"])
        resp = self.client.get("/", HTTP_HOST="sectionorder.test")
        self.assertEqual(resp.status_code, 200)
