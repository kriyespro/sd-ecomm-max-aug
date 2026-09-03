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
    reset_and_seed,
    seed_starter_content,
    wipe_storefront_content,
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


class WipeAndResetTests(TestCase):
    def _order_for(self, project, product):
        from apps.orders.models import Order, OrderItem

        o = Order.objects.create(
            project=project, number="T-1", email="b@x.test",
            subtotal=0, discount_total=0, tax_total=0, shipping_total=0,
            grand_total=0,
        )
        OrderItem.objects.create(
            order=o, product=product, product_title=product.title,
            unit_price=product.price, quantity=1, line_total=product.price,
        )
        return o

    def test_wipe_clears_content_but_keeps_orders(self):
        p = _project()
        seed_starter_content(p)
        prod = Product.objects.filter(project=p).first()
        order = self._order_for(p, prod)

        counts = wipe_storefront_content(p)

        self.assertFalse(Product.objects.filter(project=p).exists())
        self.assertFalse(Category.objects.filter(project=p).exists())
        self.assertFalse(Banner.objects.filter(project=p).exists())
        self.assertGreaterEqual(counts["products"], 8)

        order.refresh_from_db()
        self.assertTrue(order.pk)  # order kept
        self.assertIsNone(order.items.first().product_id)  # link nulled, snapshot stays
        self.assertEqual(order.items.first().product_title, prod.title)

    def test_wipe_releases_carts_that_protect_products(self):
        from apps.cart.models import Cart, CartItem

        p = _project()
        seed_starter_content(p)
        prod = Product.objects.filter(project=p).first()
        cart = Cart.objects.create(project=p, session_key="s1")
        CartItem.objects.create(cart=cart, product=prod, quantity=1,
                                unit_price=prod.price)

        wipe_storefront_content(p)  # must not raise ProtectedError

        self.assertFalse(Cart.objects.filter(project=p).exists())
        self.assertFalse(Product.objects.filter(project=p).exists())

    def test_reset_and_seed_gives_a_fresh_set(self):
        p = _project()
        Category.objects.create(project=p, name="Old junk")
        Product.objects.create(project=p, title="Old junk", price=1, status="active")

        reset_and_seed(p)

        self.assertEqual(Product.objects.filter(project=p).count(), 8)
        self.assertFalse(Product.objects.filter(project=p, title="Old junk").exists())
        self.assertTrue(is_seeded(p))


class DemoContentImportViewTests(TestCase):
    def setUp(self):
        from apps.billing.models import Plan

        self.admin = get_user_model().objects.create_superuser("root", "r@t.test", "pw")
        self.plan = Plan.objects.filter(is_active=True).order_by("sort_order").first()
        self.project = _project("Import Target")
        Product.objects.create(project=self.project, title="Real", price=9, status="active")
        self.client.force_login(self.admin)
        session = self.client.session
        session["active_project_id"] = self.project.pk
        session.save()

    def test_wrong_confirm_changes_nothing(self):
        resp = self.client.post("/admin/cms/demo-content/import/", {"confirm": "delete"})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Product.objects.filter(project=self.project, title="Real").exists())
        self.assertFalse(is_seeded(self.project))

    def test_confirmed_import_wipes_and_seeds(self):
        resp = self.client.post("/admin/cms/demo-content/import/", {"confirm": "DELETE"})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Product.objects.filter(project=self.project, title="Real").exists())
        self.assertEqual(Product.objects.filter(project=self.project).count(), 8)
        self.project.refresh_from_db()
        self.assertTrue(is_seeded(self.project))


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


class DemoFlagAndPlaceholderRenderTests(TestCase):
    def setUp(self):
        # store_chrome() is cache-backed and the cache outlives a TestCase's
        # transaction rollback — clear it so a reused pk can't serve a prior
        # test's chrome.
        from django.core.cache import cache

        cache.clear()

    def _render_home(self, project, skin="default"):
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import RequestFactory

        from apps.shopfront import views
        from apps.shopfront.runtime import use_skin

        rq = RequestFactory().get("/")
        rq.project, rq.user, rq.session = project, AnonymousUser(), SessionStore()
        rq.skin_slug = skin
        with use_skin(skin):
            return views.HomeView().get(rq).content.decode()

    def test_chrome_exposes_demo_flag(self):
        from apps.core.store_resolver import store_chrome

        p = _project()
        self.assertFalse(store_chrome(p)["demo"])
        seed_starter_content(p)
        self.assertTrue(store_chrome(p)["demo"])

    def test_demo_store_shows_logo_placeholder_not_wordmark(self):
        p = _project("Placeholder Store")
        seed_starter_content(p)
        html = self._render_home(p)
        self.assertIn("data:image/svg+xml", html)
        self.assertIn("Your%20logo", html)

    def test_non_demo_store_keeps_text_wordmark(self):
        p = _project("Plain Store")
        html = self._render_home(p)
        self.assertNotIn("Your%20logo", html)
        self.assertIn("Plain Store", html)
