""""Buy now" on the product page: adds to cart exactly like "Add to bag",
but the response tells htmx to redirect straight to checkout instead of
opening the cart drawer."""

from django.test import TestCase, override_settings

from apps.cart.models import Cart
from apps.catalog.models import Product
from apps.projects.models import Domain, Project


@override_settings(ALLOWED_HOSTS=["*"])
class BuyNowTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="BuyNowCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="buynowco.test", is_verified=True)
        self.product = Product.objects.create(
            project=self.project, title="Ring", slug="ring", status="active", price="999",
        )

    def test_product_page_shows_buy_now_before_add_to_bag(self):
        body = self.client.get("/p/ring/", HTTP_HOST="buynowco.test").content.decode()
        buy_now_pos = body.index("Buy now")
        add_to_bag_pos = body.index("Add to bag")
        self.assertLess(buy_now_pos, add_to_bag_pos)

    def test_sticky_mobile_bar_also_has_buy_now(self):
        body = self.client.get("/p/ring/", HTTP_HOST="buynowco.test").content.decode()
        self.assertEqual(body.count("Buy now"), 2)
        self.assertEqual(body.count("Add to bag"), 2)

    def test_buy_now_adds_to_cart_and_redirects_to_checkout(self):
        resp = self.client.post(
            "/cart/add/", {"product": "ring", "quantity": "1", "buy_now": "1"},
            HTTP_HOST="buynowco.test",
        )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(resp["HX-Redirect"], "/checkout/")
        cart = Cart.objects.get(project=self.project)
        self.assertEqual(cart.items.count(), 1)
        self.assertEqual(cart.items.first().product_id, self.product.pk)

    def test_plain_add_to_cart_is_unaffected(self):
        resp = self.client.post(
            "/cart/add/", {"product": "ring", "quantity": "1"}, HTTP_HOST="buynowco.test",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("HX-Redirect", resp)
        self.assertIn("HX-Trigger", resp)
