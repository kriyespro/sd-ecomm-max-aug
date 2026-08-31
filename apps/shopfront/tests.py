from decimal import Decimal

from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase

from apps.cart.models import Cart, CartItem
from apps.catalog.models import Product
from apps.projects.models import Project
from apps.shopfront.context import base_context


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
