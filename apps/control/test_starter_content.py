"""Demo / starter storefront content: seeding, idempotency, removal, and the
placeholder helper the skins lean on."""

from urllib.parse import unquote

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.catalog.models import Product
from apps.categories.models import Category
from apps.cms.models import Banner, Page
from apps.control.starter_content import (
    is_seeded,
    remove_starter_content,
    seed_starter_content,
)
from apps.media.placeholders import media_src, svg_placeholder
from apps.projects.models import Project
from apps.reviews.models import Review


def _project(name="Demo Co"):
    return Project.objects.create(
        name=name, currency="INR", country="IN", status=Project.Status.ACTIVE
    )


class SeedStarterContentTests(TestCase):
    def test_seed_creates_editable_rows_and_flag(self):
        p = _project()
        ref = seed_starter_content(p)

        self.assertEqual(Category.objects.filter(project=p).count(), 6)
        self.assertEqual(Product.objects.filter(project=p, status="active").count(), 8)
        self.assertEqual(Banner.objects.filter(project=p).count(), 6)
        self.assertEqual(Page.objects.filter(project=p).count(), 5)
        self.assertTrue(Review.objects.filter(project=p).exists())

        p.refresh_from_db()
        self.assertTrue(is_seeded(p))
        self.assertEqual(set(ref), {"categories", "products", "banners", "pages", "reviews"})

    def test_products_have_text_but_no_images(self):
        p = _project()
        seed_starter_content(p)
        for prod in Product.objects.filter(project=p):
            self.assertTrue(prod.short_description)
            self.assertTrue(prod.description)
            self.assertFalse(prod.images.exists())
        # a hero banner with copy, no image file
        hero = Banner.objects.get(project=p, placement="hero")
        self.assertTrue(hero.heading)
        self.assertFalse(hero.image)

    def test_seed_is_idempotent(self):
        p = _project()
        seed_starter_content(p)
        seed_starter_content(p)
        self.assertEqual(Product.objects.filter(project=p).count(), 8)

    def test_force_reseeds(self):
        p = _project()
        seed_starter_content(p)
        seed_starter_content(p, force=True)
        # categories are get_or_create by name -> still 6, products append -> 16
        self.assertEqual(Category.objects.filter(project=p).count(), 6)
        self.assertEqual(Product.objects.filter(project=p).count(), 16)

    def test_remove_deletes_only_seeded_rows(self):
        p = _project()
        seed_starter_content(p)
        owner_cat = Category.objects.create(project=p, name="My Own Category")
        owner_prod = Product.objects.create(
            project=p, title="My Own Product", price=100, status="active"
        )

        remove_starter_content(p)
        p.refresh_from_db()

        self.assertFalse(is_seeded(p))
        self.assertEqual(list(Category.objects.filter(project=p)), [owner_cat])
        self.assertEqual(list(Product.objects.filter(project=p)), [owner_prod])
        self.assertFalse(Banner.objects.filter(project=p).exists())

    def test_reviews_refresh_product_rating(self):
        p = _project()
        seed_starter_content(p)
        rated = Product.objects.filter(project=p, rating_count__gt=0)
        self.assertTrue(rated.exists())
        for prod in rated:
            self.assertGreater(prod.rating_avg, 0)


class CreateStoreSeedsDemoTests(TestCase):
    def test_new_store_gets_demo_content_on_commit(self):
        from apps.billing.models import BillingPeriod, Plan
        from apps.control.store_services import create_store

        plan = Plan.objects.filter(is_active=True).order_by("sort_order").first()
        actor = get_user_model().objects.create_superuser("root", "root@t.test", "pw")

        with self.captureOnCommitCallbacks(execute=True):
            project, _owner, _created = create_store(
                name="Seeded Store", owner_email="o@seed.test", plan=plan,
                actor=actor, period=BillingPeriod.MONTHLY,
            )

        project.refresh_from_db()
        self.assertTrue(is_seeded(project))
        self.assertEqual(Product.objects.filter(project=project).count(), 8)


class PlaceholderHelperTests(TestCase):
    def test_svg_placeholder_is_a_valid_data_uri(self):
        import xml.dom.minidom as minidom

        uri = svg_placeholder(1600, 900, "Hero banner")
        self.assertTrue(uri.startswith("data:image/svg+xml;charset=utf-8,"))
        svg = unquote(uri.split(",", 1)[1])
        minidom.parseString(svg)  # raises on malformed XML
        self.assertIn("1600×900", svg)
        self.assertIn("HERO BANNER", svg)

    def test_svg_placeholder_handles_bad_input(self):
        svg = unquote(svg_placeholder("x", None).split(",", 1)[1])
        self.assertIn("1200×800", svg)

    def test_media_src_prefers_real_url(self):
        self.assertEqual(media_src("/media/a.jpg", 10, 10), "/media/a.jpg")
        self.assertTrue(media_src(None, 10, 10).startswith("data:image/svg+xml"))
        self.assertTrue(media_src("", 10, 10).startswith("data:image/svg+xml"))
