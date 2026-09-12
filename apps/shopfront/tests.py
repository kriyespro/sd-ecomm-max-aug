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


@override_settings(ALLOWED_HOSTS=["*"])
class PerStoreSignupIsolationTests(TestCase):
    """A shopper's login is scoped per store going forward — registering at
    Store B with an email already used at Store A creates an independent
    account there instead of being blocked (or, previously, silently sharing
    the same global password across every store on the platform)."""

    def setUp(self):
        from apps.projects.models import Domain

        self.store_a = Project.objects.create(name="Store A", status="active", currency="INR")
        Domain.objects.create(project=self.store_a, host="a.iso.test", is_verified=True)
        self.store_b = Project.objects.create(name="Store B", status="active", currency="INR")
        Domain.objects.create(project=self.store_b, host="b.iso.test", is_verified=True)

    def _register(self, host, email, password):
        return self.client.post(
            "/account/register/", {"email": email, "password": password}, HTTP_HOST=host,
        )

    def _login(self, host, email, password):
        return self.client.post(
            "/account/login/", {"email": email, "password": password}, HTTP_HOST=host,
        )

    def test_same_email_can_register_independently_at_a_second_store(self):
        r1 = self._register("a.iso.test", "shop@buy.test", "Str0ngPassw0rdA!")
        self.assertEqual(r1.status_code, 302)
        self.client.logout()
        r2 = self._register("b.iso.test", "shop@buy.test", "Str0ngPassw0rdB!")
        self.assertEqual(r2.status_code, 302)

        User = get_user_model()
        self.assertEqual(User.objects.filter(email__iexact="shop@buy.test").count(), 2)

    def test_registering_twice_at_the_same_store_is_blocked(self):
        self._register("a.iso.test", "dup@buy.test", "Str0ngPassw0rdA!")
        self.client.logout()
        self._register("a.iso.test", "dup@buy.test", "Str0ngPassw0rdB!")
        resp = self.client.get("/account/", HTTP_HOST="a.iso.test")
        self.assertContains(resp, "Check your inbox")

        User = get_user_model()
        self.assertEqual(User.objects.filter(email__iexact="dup@buy.test").count(), 1)

    def test_passwords_are_fully_isolated_between_stores(self):
        self._register("a.iso.test", "iso@buy.test", "Str0ngPassw0rdA!")
        self.client.logout()
        self._register("b.iso.test", "iso@buy.test", "Str0ngPassw0rdB!")
        self.client.logout()

        # Store A's password must not work at Store B.
        self._login("b.iso.test", "iso@buy.test", "Str0ngPassw0rdA!")
        resp = self.client.get("/account/", HTTP_HOST="b.iso.test")
        self.assertNotContains(resp, "iso@buy.test")  # never actually got in

        # Store B's own password does.
        self._login("b.iso.test", "iso@buy.test", "Str0ngPassw0rdB!")
        resp2 = self.client.get("/account/", HTTP_HOST="b.iso.test")
        self.assertContains(resp2, "iso@buy.test")

    def test_legacy_preexisting_account_still_logs_into_any_store(self):
        """An account created before per-store signup shipped (plain email as
        username, no store suffix) must keep working everywhere it always
        did — only NEW registrations get isolated."""
        User = get_user_model()
        User.objects.create_user(
            username="legacy@buy.test", email="legacy@buy.test", password="Str0ngPassw0rdL!",
        )
        resp_a = self._login("a.iso.test", "legacy@buy.test", "Str0ngPassw0rdL!")
        self.assertEqual(resp_a.status_code, 302)
        self.client.logout()
        resp_b = self._login("b.iso.test", "legacy@buy.test", "Str0ngPassw0rdL!")
        self.assertEqual(resp_b.status_code, 302)
        page = self.client.get("/account/", HTTP_HOST="b.iso.test")
        self.assertContains(page, "legacy@buy.test")


class GuestCartMergeTests(TestCase):
    """Items added to an anonymous session cart must survive signing in or
    registering — the view looks the cart up by ``user`` afterward, never
    ``session_key`` again, so without an explicit merge they silently vanish."""

    def setUp(self):
        from apps.projects.models import Domain

        self.project = Project.objects.create(name="MergeShop", status="active", currency="INR")
        Domain.objects.create(project=self.project, host="merge.shop.test", is_verified=True)
        self.product = Product.objects.create(
            project=self.project, title="Tote", price=Decimal("500"), status="active",
        )

    def _add_as_guest(self):
        return self.client.post(
            "/cart/add/", {"product": self.product.slug, "quantity": "2"},
            HTTP_HOST="merge.shop.test",
        )

    def test_login_merges_the_anonymous_cart(self):
        User = get_user_model()
        user = User.objects.create_user(username="s2@buy.test", email="s2@buy.test", password="pw")
        self._add_as_guest()

        resp = self.client.post(
            "/account/login/", {"email": "s2@buy.test", "password": "pw"},
            HTTP_HOST="merge.shop.test",
        )
        self.assertEqual(resp.status_code, 302)

        cart = Cart.objects.get(project=self.project, user=user, is_active=True)
        self.assertEqual(cart.items.count(), 1)
        self.assertEqual(cart.items.first().quantity, 2)

    def test_login_merges_into_an_existing_account_cart(self):
        from apps.cart.services import get_or_create_cart

        User = get_user_model()
        user = User.objects.create_user(username="s3@buy.test", email="s3@buy.test", password="pw")
        existing_cart = get_or_create_cart(project=self.project, user=user)
        CartItem.objects.create(
            cart=existing_cart, product=self.product, quantity=1,
            unit_price=self.product.price,
        )
        self._add_as_guest()

        self.client.post(
            "/account/login/", {"email": "s3@buy.test", "password": "pw"},
            HTTP_HOST="merge.shop.test",
        )
        existing_cart.refresh_from_db()
        self.assertEqual(existing_cart.items.get().quantity, 3)

    def test_register_merges_the_anonymous_cart(self):
        self._add_as_guest()
        resp = self.client.post(
            "/account/register/",
            {"email": "new@buy.test", "password": "Str0ngPassw0rd!"},
            HTTP_HOST="merge.shop.test",
        )
        self.assertEqual(resp.status_code, 302)
        user = get_user_model().objects.get(email="new@buy.test")
        cart = Cart.objects.get(project=self.project, user=user, is_active=True)
        self.assertEqual(cart.items.get().quantity, 2)


class OrderPayRetryTests(TestCase):
    """The checkout flow tells a shopper whose gateway payment failed "you can
    pay for it from the order page" — that page must actually offer a way to
    do that, not just a static receipt."""

    def setUp(self):
        from apps.orders.models import Order
        from apps.payments.models import Payment, PaymentProviderConfig

        self.project = Project.objects.create(name="RetryShop", status="active", currency="INR")
        from apps.projects.models import Domain
        Domain.objects.create(project=self.project, host="retry.shop.test", is_verified=True)
        PaymentProviderConfig.objects.create(
            project=self.project, provider="razorpay", is_enabled=True, is_test_mode=True,
            credentials={"key_id": "rzp_test_k", "key_secret": "sec"},
        )
        self.order = Order.objects.create(
            project=self.project, number="RETRY-1", email="x@t.test",
            status="pending", payment_status="pending",
            subtotal=Decimal("500"), grand_total=Decimal("500"),
        )
        Payment.objects.create(
            project=self.project, order=self.order, provider="razorpay",
            amount=Decimal("500"), currency="INR", status="failed",
        )
        session = self.client.session
        session["shopfront_orders"] = [self.order.number]
        session.save()

    def test_retry_opens_a_new_gateway_attempt(self):
        resp = self.client.post(
            f"/order/{self.order.number}/pay/", HTTP_HOST="retry.shop.test",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Completing your payment")

    def test_cod_order_cannot_be_retried(self):
        from apps.payments.models import Payment

        self.order.payments.all().delete()
        Payment.objects.create(
            project=self.project, order=self.order, provider="cod",
            amount=Decimal("500"), currency="INR",
        )
        resp = self.client.post(
            f"/order/{self.order.number}/pay/", HTTP_HOST="retry.shop.test", follow=True,
        )
        self.assertContains(resp, "can no longer be paid online")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending")

    def test_already_paid_order_cannot_be_retried(self):
        self.order.payment_status = "paid"
        self.order.save(update_fields=["payment_status"])
        resp = self.client.post(
            f"/order/{self.order.number}/pay/", HTTP_HOST="retry.shop.test", follow=True,
        )
        self.assertContains(resp, "can no longer be paid online")

    def test_order_page_shows_retry_button_when_eligible(self):
        resp = self.client.get(f"/order/{self.order.number}/", HTTP_HOST="retry.shop.test")
        self.assertContains(resp, "Retry payment")


class CheckoutRazorpayTests(TestCase):
    def setUp(self):
        from apps.projects.models import Domain
        from apps.payments.models import PaymentProviderConfig

        self.project = Project.objects.create(name="PayShop", status="active", currency="INR")
        Domain.objects.create(project=self.project, host="pay.shop.test", is_verified=True)
        self.product = Product.objects.create(
            project=self.project, title="Ring", price=Decimal("1000"),
        )
        PaymentProviderConfig.objects.create(
            project=self.project, provider="razorpay", is_enabled=True, is_test_mode=True,
            credentials={"key_id": "rzp_test_k", "key_secret": "sec"},
        )
        User = get_user_model()
        self.user = User.objects.create_user("shopper", "s@buy.test", "pw")
        self.client.force_login(self.user)

    def _add_to_cart(self):
        from apps.cart.services import get_or_create_cart
        cart = get_or_create_cart(project=self.project, user=self.user)
        CartItem.objects.create(cart=cart, product=self.product, quantity=1,
                                unit_price=self.product.price)
        return cart

    def test_providers_helper_orders_razorpay_before_cod_and_drops_manual(self):
        from apps.shopfront.views import _checkout_payment_providers
        keys = [p["key"] for p in _checkout_payment_providers(self.project)]
        self.assertEqual(keys, ["razorpay", "cod"])
        self.assertNotIn("manual", keys)

    def test_checkout_page_offers_razorpay_first_with_branding(self):
        self._add_to_cart()
        resp = self.client.get("/checkout/", HTTP_HOST="pay.shop.test")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="razorpay"')
        self.assertContains(resp, "Pay with Razorpay")
        body = resp.content.decode()
        self.assertLess(body.index('value="razorpay"'), body.index('value="cod"'))

    def test_razorpay_checkout_renders_pay_page_and_places_order(self):
        from apps.orders.models import Order
        from apps.payments.models import Payment

        self._add_to_cart()
        resp = self.client.post("/checkout/", {
            "email": "s@buy.test", "name": "Shopper", "line1": "1 St",
            "city": "Pune", "postal_code": "411001", "country": "IN", "phone": "9990001234",
            "payment_method": "razorpay",
        }, HTTP_HOST="pay.shop.test")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "checkout.razorpay.com/v1/checkout.js")
        self.assertContains(resp, "rzp_test_k")
        self.assertContains(resp, "Completing your payment")

        order = Order.objects.get(project=self.project)
        self.assertEqual(order.payment_status, "pending")
        pmt = Payment.objects.get(order=order)
        self.assertEqual(pmt.provider, "razorpay")
        self.assertContains(resp, str(pmt.pk))
