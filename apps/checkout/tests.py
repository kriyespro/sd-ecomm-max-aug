"""Checkout payment-method gating (a shopper must not settle their own order)."""

from decimal import Decimal

from django.test import TestCase

from apps.cart.models import Cart, CartItem
from apps.catalog.models import Product
from apps.coupons.models import Coupon, DiscountType
from apps.payments.models import Payment, PaymentProviderConfig, Provider
from apps.payments.providers.manual import ManualProvider
from apps.projects.models import Project
from apps.shipping.models import RateType, ShippingMethod, ShippingZone

from .services import CheckoutError, complete_checkout, _validate_payment_method


class PaymentMethodGateTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Acme")

    def _enable(self, provider):
        PaymentProviderConfig.objects.create(
            project=self.project, provider=provider, is_enabled=True
        )

    def test_blank_method_is_allowed(self):
        _validate_payment_method(self.project, "")  # no raise

    def test_manual_is_never_selectable_even_when_enabled(self):
        self._enable(Provider.MANUAL)
        with self.assertRaises(CheckoutError):
            _validate_payment_method(self.project, "manual")

    def test_method_not_enabled_is_rejected(self):
        with self.assertRaises(CheckoutError):
            _validate_payment_method(self.project, "razorpay")

    def test_enabled_gateway_passes(self):
        self._enable(Provider.RAZORPAY)
        _validate_payment_method(self.project, "razorpay")  # no raise

    def test_manual_provider_never_self_verifies(self):
        self.assertFalse(ManualProvider(config=None).verify(payment=None, data={}))


class ShippingBeforeCouponAndPaymentTests(TestCase):
    """Shipping must be priced onto the order before the coupon is applied and
    before a COD/offline payment snapshots grand_total — otherwise a
    FREE_SHIPPING coupon quotes against 0 and a COD collection undercharges
    for shipping forever."""

    def setUp(self):
        self.project = Project.objects.create(name="ShipCo")
        self.product = Product.objects.create(
            project=self.project, title="Widget", price=Decimal("500")
        )
        self.address = {
            "name": "Jane", "line1": "1 Main St", "city": "Town",
            "postal_code": "560001", "country": "IN", "phone": "9999999999",
        }
        zone = ShippingZone.objects.create(project=self.project, name="All")
        self.method = ShippingMethod.objects.create(
            project=self.project, zone=zone, name="Standard",
            rate_type=RateType.FLAT, base_rate=Decimal("100"),
        )

    def _cart(self):
        cart = Cart.objects.create(project=self.project, is_active=True)
        CartItem.objects.create(
            cart=cart, product=self.product, quantity=1, unit_price=self.product.price
        )
        return cart

    def test_free_shipping_coupon_actually_zeroes_the_shipping_charge(self):
        coupon = Coupon.objects.create(
            project=self.project, code="FREESHIP",
            discount_type=DiscountType.FREE_SHIPPING,
        )
        order, _ = complete_checkout(
            project=self.project, cart=self._cart(), email="j@t.test",
            shipping_address=self.address, phone=self.address["phone"],
            coupon_code=coupon.code, payment_method=Provider.COD,
            shipping_method=self.method,
        )
        self.assertEqual(order.shipping_total, Decimal("100"))
        self.assertEqual(order.discount_total, Decimal("100"))
        self.assertEqual(order.grand_total, order.subtotal)

    def test_cod_payment_amount_includes_shipping(self):
        order, _ = complete_checkout(
            project=self.project, cart=self._cart(), email="j@t.test",
            shipping_address=self.address, phone=self.address["phone"],
            payment_method=Provider.COD, shipping_method=self.method,
        )
        payment = Payment.objects.get(order=order)
        self.assertEqual(order.shipping_total, Decimal("100"))
        self.assertEqual(payment.amount, order.grand_total)
        self.assertEqual(payment.amount, order.subtotal + Decimal("100"))
