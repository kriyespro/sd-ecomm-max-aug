from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from apps.cart.models import Cart, CartItem
from apps.catalog.models import Product
from apps.projects.models import Project
from apps.shopfront.context import base_context, get_cart
from apps.shopfront.middleware import NoStoreStorefrontMiddleware


class BaseContextCartTests(TestCase):
    """base_context primes cart.items so templates skip re-querying. The cache
    must stay a queryset — a plain list broke cart.items.exists() (checkout 500)
    and .select_related() (ornza cart)."""

    def setUp(self):
        self.project = Project.objects.create(
            name="CtxShop", status="active", currency="INR"
        )
        self.product = Product.objects.create(project=self.project, title="Gold Ring")

    def _request_with_cart(self, *, empty=False):
        req = RequestFactory().get("/checkout/")
        req.project = self.project
        req.user = AnonymousUser()
        req.session = SessionStore()
        req.session.save()
        req.skin_slug = "default"
        cart = Cart.objects.create(
            project=self.project, session_key=req.session.session_key
        )
        if not empty:
            CartItem.objects.create(
                cart=cart, product=self.product, quantity=2,
                unit_price=Decimal("1999.00"),
            )
        return req

    def test_primed_cart_items_still_supports_manager_methods(self):
        cart = base_context(self._request_with_cart(), self.project)["cart"]
        self.assertTrue(cart.items.exists())
        self.assertEqual(cart.items.count(), 1)
        self.assertEqual([i.quantity for i in cart.items.all()], [2])
        self.assertEqual(
            cart.items.select_related("product").first().product_id, self.product.pk
        )
        self.assertEqual(cart.item_count, 2)

    def test_empty_cart_reports_empty(self):
        cart = base_context(self._request_with_cart(empty=True), self.project)["cart"]
        self.assertFalse(cart.items.exists())
        self.assertEqual(cart.items.count(), 0)

    def test_priming_costs_no_extra_query(self):
        cart = base_context(self._request_with_cart(), self.project)["cart"]
        with self.assertNumQueries(0):
            list(cart.items.all())
            cart.items.exists()
            cart.items.count()


class DeferredSessionCartTests(TestCase):
    """A read-only storefront render must not spawn a session or a Cart row for
    an anonymous visitor — that keeps the response cookie-free / edge-cacheable.
    A cart mutation does create both."""

    def setUp(self):
        self.project = Project.objects.create(name="DeferShop", status="active", currency="INR")

    def _req(self):
        req = RequestFactory().get("/")
        req.user = AnonymousUser()
        req.session = SessionStore()
        return req

    def test_read_starts_no_session_no_cart(self):
        req = self._req()
        cart = get_cart(req, self.project)
        self.assertTrue(getattr(cart, "_is_empty", False))
        self.assertIsNone(req.session.session_key)
        self.assertFalse(Cart.objects.filter(project=self.project).exists())

    def test_empty_cart_renders_through_base_context(self):
        req = self._req()
        req.skin_slug = "default"
        ctx = base_context(req, self.project)
        self.assertFalse(ctx["cart"].items.exists())
        self.assertEqual(ctx["cart_count"], 0)
        self.assertEqual(ctx["cart_subtotal"], Decimal("0.00"))

    def test_mutation_creates_session_and_cart(self):
        req = self._req()
        cart = get_cart(req, self.project, create=True)
        self.assertIsNotNone(req.session.session_key)
        self.assertTrue(Cart.objects.filter(pk=cart.pk).exists())


@override_settings(DEBUG=False, ALLOWED_HOSTS=["*"])
class EdgeCacheHeaderTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="EdgeShop", status="active")

    def _req(self, path="/", method="get", **extra):
        req = getattr(RequestFactory(), method)(path, HTTP_HOST="shop.edge.test", **extra)
        req.storefront_host = True
        req.user = AnonymousUser()
        return req

    def _run(self, req, response=None):
        response = response or HttpResponse("ok")
        return NoStoreStorefrontMiddleware(lambda r: response)(req)

    def test_anon_public_page_is_edge_cacheable(self):
        resp = self._run(self._req("/"))
        self.assertIn("s-maxage=180", resp["Cache-Control"])
        self.assertEqual(resp["X-Storefront-Cache"], "public")

    def test_session_cookie_forces_private(self):
        req = self._req("/")
        req.COOKIES["sessionid"] = "abc"
        self.assertEqual(self._run(req)["X-Storefront-Cache"], "private")

    def test_authenticated_forces_private(self):
        req = self._req("/")
        req.user = get_user_model().objects.create_user("shopper", password="x")
        self.assertEqual(self._run(req)["X-Storefront-Cache"], "private")

    def test_cart_path_is_private(self):
        self.assertEqual(self._run(self._req("/cart/"))["X-Storefront-Cache"], "private")

    def test_set_cookie_response_not_cached(self):
        r = HttpResponse("ok")
        r.set_cookie("csrftoken", "t")
        self.assertEqual(self._run(self._req("/"), r)["X-Storefront-Cache"], "private")

    @override_settings(DEBUG=True)
    def test_debug_is_no_store(self):
        self.assertIn("no-store", self._run(self._req("/"))["Cache-Control"])


@override_settings(ALLOWED_HOSTS=["*"])
class StorefrontCartCsrfBypassTests(TestCase):
    def _post(self, path="/cart/add/", **extra):
        req = RequestFactory().post(path, HTTP_HOST="shop.edge.test", **extra)
        req.storefront_host = True
        req.user = AnonymousUser()
        NoStoreStorefrontMiddleware(lambda r: HttpResponse())(req)
        return req

    def test_same_origin_no_cookie_bypasses_csrf(self):
        req = self._post(HTTP_ORIGIN="http://shop.edge.test")
        self.assertTrue(getattr(req, "csrf_processing_done", False))

    def test_cross_origin_not_bypassed(self):
        req = self._post(HTTP_ORIGIN="http://evil.test")
        self.assertFalse(getattr(req, "csrf_processing_done", False))

    def test_no_origin_or_referer_not_bypassed(self):
        req = self._post()
        self.assertFalse(getattr(req, "csrf_processing_done", False))

    def test_existing_csrf_cookie_keeps_normal_check(self):
        req = self._post(HTTP_ORIGIN="http://shop.edge.test")
        # (cookie set before the middleware runs)
        req2 = RequestFactory().post("/cart/add/", HTTP_HOST="shop.edge.test",
                                     HTTP_ORIGIN="http://shop.edge.test")
        req2.storefront_host = True
        req2.user = AnonymousUser()
        req2.COOKIES["csrftoken"] = "existing"
        NoStoreStorefrontMiddleware(lambda r: HttpResponse())(req2)
        self.assertFalse(getattr(req2, "csrf_processing_done", False))

    def test_non_cart_path_not_bypassed(self):
        req = self._post(path="/checkout/", HTTP_ORIGIN="http://shop.edge.test")
        self.assertFalse(getattr(req, "csrf_processing_done", False))
