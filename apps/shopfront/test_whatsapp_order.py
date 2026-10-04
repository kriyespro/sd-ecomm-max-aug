"""Order on WhatsApp checkout + WhatsApp-only store.

The order is always saved first (no payment row, stays PENDING); the thank-you
page then hands the shopper to wa.me with the order pre-filled. Off by default.
"""

from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.cart.models import CartItem
from apps.cms.models import StoreProfile
from apps.catalog.models import Product
from apps.orders.models import Order
from apps.projects.models import Domain, Project
from apps.shopfront import whatsapp_order as wa

HOST = "wa.shop.test"
FORM = {
    "email": "s@buy.test", "name": "Riya Sharma", "line1": "12 MG Road",
    "city": "Pune", "postal_code": "411001", "country": "IN", "phone": "9990001234",
}


@override_settings(ALLOWED_HOSTS=["*"])
class WhatsAppOrderTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="WA Shop", status="active", currency="INR")
        Domain.objects.create(project=self.project, host=HOST, is_verified=True)
        self.product = Product.objects.create(
            project=self.project, title="Choco Cake", price=Decimal("450"),
        )
        self.user = get_user_model().objects.create_user("shopper", "s@buy.test", "pw")
        self.client.force_login(self.user)

    def _profile(self, **kw):
        kw.setdefault("whatsapp", "+91 98123 45678")
        return StoreProfile.objects.update_or_create(project=self.project, defaults=kw)[0]

    def _cart(self, qty=2):
        from apps.cart.services import get_or_create_cart
        cart = get_or_create_cart(project=self.project, user=self.user)
        CartItem.objects.create(cart=cart, product=self.product, quantity=qty,
                                unit_price=self.product.price)

    def _post(self, **extra):
        return self.client.post("/checkout/", {**FORM, **extra}, HTTP_HOST=HOST)

    # --- option visibility ---------------------------------------------
    def test_off_by_default(self):
        from apps.shopfront.views import _checkout_payment_providers
        self._profile()
        self.assertNotIn("whatsapp", [p["key"] for p in _checkout_payment_providers(self.project)])

    def test_enabled_adds_option_last(self):
        from apps.shopfront.views import _checkout_payment_providers
        self._profile(whatsapp_order_enabled=True)
        keys = [p["key"] for p in _checkout_payment_providers(self.project)]
        self.assertEqual(keys, ["cod", "whatsapp"])

    def test_whatsapp_only_is_sole_option(self):
        from apps.shopfront.views import _checkout_payment_providers
        self._profile(whatsapp_only=True)
        keys = [p["key"] for p in _checkout_payment_providers(self.project)]
        self.assertEqual(keys, ["whatsapp"])

    def test_flag_without_valid_number_is_ignored(self):
        self._profile(whatsapp_order_enabled=True, whatsapp="")
        self.assertIsNone(wa.ordering_profile(self.project))
        self._profile(whatsapp_order_enabled=True, whatsapp="12")
        self.assertIsNone(wa.ordering_profile(self.project))

    # --- placing the order ---------------------------------------------
    def test_whatsapp_order_saved_pending_without_payment(self):
        self._profile(whatsapp_order_enabled=True)
        self._cart()
        resp = self._post(payment_method="whatsapp")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].endswith("?wa=1"))
        order = Order.objects.get(project=self.project)
        self.assertEqual(order.status, "pending")
        self.assertFalse(order.payments.exists())
        self.assertEqual(order.admin_note, "Placed via WhatsApp checkout")
        self.assertEqual(order.grand_total, Decimal("900"))

    def test_post_whatsapp_when_disabled_is_rejected(self):
        self._profile()
        self._cart()
        resp = self._post(payment_method="whatsapp")
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Order.objects.filter(project=self.project).exists())

    def test_whatsapp_only_forces_whatsapp_even_if_cod_posted(self):
        self._profile(whatsapp_only=True)
        self._cart()
        resp = self._post(payment_method="cod")
        self.assertTrue(resp["Location"].endswith("?wa=1"))
        order = Order.objects.get(project=self.project)
        self.assertFalse(order.payments.exists())

    def test_whatsapp_only_checkout_page_has_no_cod(self):
        self._profile(whatsapp_only=True)
        self._cart()
        resp = self.client.get("/checkout/", HTTP_HOST=HOST)
        self.assertContains(resp, 'value="whatsapp"')
        self.assertNotContains(resp, 'value="cod"')
        self.assertContains(resp, "Order on WhatsApp")

    def test_normal_cod_unaffected_when_whatsapp_enabled(self):
        self._profile(whatsapp_order_enabled=True)
        self._cart()
        self._post(payment_method="cod")
        order = Order.objects.get(project=self.project)
        self.assertEqual(order.payments.get().provider, "cod")
        self.assertEqual(order.status, "confirmed")

    # --- hand-off page ---------------------------------------------------
    def test_order_page_has_wa_link_with_prefilled_message(self):
        self._profile(whatsapp_order_enabled=True)
        self._cart()
        loc = self._post(payment_method="whatsapp")["Location"]
        resp = self.client.get(loc, HTTP_HOST=HOST)
        self.assertContains(resp, "Send order on WhatsApp")
        order = Order.objects.get(project=self.project)
        link = wa.build_link(StoreProfile.objects.get(project=self.project), order, "WA Shop")
        self.assertContains(resp, link.replace("&", "&amp;") if "&" in link else link)
        parsed = urlparse(link)
        self.assertEqual(parsed.netloc, "wa.me")
        self.assertEqual(parsed.path, "/919812345678")
        text = parse_qs(parsed.query)["text"][0]
        self.assertIn(order.number, text)
        self.assertIn("2 x Choco Cake", text)
        self.assertIn("Riya Sharma", text)

    def test_cod_order_page_has_no_wa_button(self):
        self._profile(whatsapp_order_enabled=True)
        self._cart()
        loc = self._post(payment_method="cod")["Location"]
        resp = self.client.get(loc, HTTP_HOST=HOST)
        self.assertNotContains(resp, "Send order on WhatsApp")

    def test_auto_open_only_with_wa_flag(self):
        self._profile(whatsapp_order_enabled=True)
        self._cart()
        loc = self._post(payment_method="whatsapp")["Location"]
        self.assertContains(self.client.get(loc, HTTP_HOST=HOST), "wa_sent_")
        self.assertNotContains(self.client.get(loc.split("?")[0], HTTP_HOST=HOST), "wa_sent_")

    def test_message_truncates_many_items(self):
        self._profile(whatsapp_order_enabled=True)
        self._cart()
        order = Order.objects.create(project=self.project, number="X1", email="a@b.test")
        from apps.orders.models import OrderItem
        for n in range(25):
            OrderItem.objects.create(order=order, product_title=f"P{n}", quantity=1,
                                     unit_price=Decimal("1"), line_total=Decimal("1"))
        msg = wa.build_message(order, "S")
        self.assertIn("+ 5 more item(s)", msg)


class WhatsAppOrderFormTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="F", status="active")

    def _form(self, number, **data):
        from apps.control.marketing_views import WhatsAppEnquiryForm
        profile = StoreProfile.objects.create(project=self.project, whatsapp=number)
        return WhatsAppEnquiryForm(data=data, instance=profile)

    def test_requires_number(self):
        self.assertFalse(self._form("", whatsapp_order_enabled=True).is_valid())

    def test_rejects_invalid_number(self):
        self.assertFalse(self._form("12", whatsapp_order_enabled=True).is_valid())

    def test_whatsapp_only_implies_enabled(self):
        form = self._form("+919812345678", whatsapp_only=True)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.save().whatsapp_order_enabled)
