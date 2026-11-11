"""Coupon discount engine: percent/fixed/free_shipping (pre-existing) plus the
new BOGO ('buy N get M free/discounted') discount type."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.test import TestCase

from apps.categories.models import Category
from apps.catalog.models import Product
from apps.customers.models import Customer
from apps.orders.models import Order, OrderItem
from apps.projects.models import Project

from .models import AppliesTo, Coupon, DiscountType
from .services import CouponError, apply_to_order, quote_discount


@dataclass
class Line:
    """Minimal duck-typed order/cart line for pure discount-math tests."""
    product_id: int
    unit_price: Decimal
    quantity: int
    product: Optional[object] = None

    @property
    def line_total(self):
        return self.unit_price * self.quantity


class BogoDiscountMathTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="BogoCo", status="active", currency="INR")

    def _coupon(self, **kwargs):
        defaults = dict(
            project=self.project, code="BOGO1", discount_type=DiscountType.BOGO,
            value=Decimal("0"), applies_to=AppliesTo.ALL,
            buy_quantity=2, get_quantity=1, get_discount_percent=Decimal("100"),
        )
        defaults.update(kwargs)
        return Coupon.objects.create(**defaults)

    def test_buy_2_get_1_free_discounts_the_cheapest_unit(self):
        coupon = self._coupon()
        lines = [
            Line(product_id=1, unit_price=Decimal("300"), quantity=1),
            Line(product_id=2, unit_price=Decimal("100"), quantity=1),
            Line(product_id=3, unit_price=Decimal("200"), quantity=1),
        ]
        discount = quote_discount(coupon, order_items=lines, subtotal=Decimal("600"))
        self.assertEqual(discount, Decimal("100.00"))

    def test_partial_get_discount_percent_halves_the_discounted_units(self):
        coupon = self._coupon(get_discount_percent=Decimal("50"))
        lines = [
            Line(product_id=1, unit_price=Decimal("100"), quantity=3),
        ]
        discount = quote_discount(coupon, order_items=lines, subtotal=Decimal("300"))
        self.assertEqual(discount, Decimal("50.00"))

    def test_only_eligible_lines_count_toward_the_group(self):
        category = Category.objects.create(project=self.project, name="Eligible", slug="eligible")
        other_category = Category.objects.create(project=self.project, name="Other", slug="other")
        eligible_product = Product.objects.create(
            project=self.project, title="Eligible", slug="eligible", category=category, price=Decimal("50"),
        )
        other_product = Product.objects.create(
            project=self.project, title="Other", slug="other", category=other_category, price=Decimal("999"),
        )
        coupon = self._coupon(applies_to=AppliesTo.CATEGORIES, buy_quantity=1, get_quantity=1)
        coupon.categories.add(category)
        lines = [
            Line(product_id=eligible_product.pk, unit_price=Decimal("50"), quantity=2, product=eligible_product),
            Line(product_id=other_product.pk, unit_price=Decimal("999"), quantity=5, product=other_product),
        ]
        discount = quote_discount(coupon, order_items=lines, subtotal=Decimal("5095"))
        self.assertEqual(discount, Decimal("50.00"))

    def test_not_enough_quantity_to_form_a_group_is_zero(self):
        coupon = self._coupon(buy_quantity=5, get_quantity=1)
        lines = [Line(product_id=1, unit_price=Decimal("100"), quantity=2)]
        discount = quote_discount(coupon, order_items=lines, subtotal=Decimal("200"))
        self.assertEqual(discount, Decimal("0"))


class BogoCheckoutApplyTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="BogoApply", status="active", currency="INR")
        self.customer = Customer.objects.create(project=self.project, email="buyer@t.test")
        self.order = Order.objects.create(
            project=self.project, number="BOGO-1", email="buyer@t.test", customer=self.customer,
        )
        OrderItem.objects.create(
            order=self.order, product_title="Tee", unit_price=Decimal("100"),
            quantity=3, line_total=Decimal("300"),
        )
        self.order.recalc_totals()

    def test_zero_discount_raises_does_not_apply(self):
        Coupon.objects.create(
            project=self.project, code="NOFIT", discount_type=DiscountType.BOGO,
            value=Decimal("0"), buy_quantity=10, get_quantity=1,
        )
        with self.assertRaises(CouponError):
            apply_to_order(order=self.order, code="NOFIT")

    def test_apply_writes_discount_total_and_recalculates_grand_total(self):
        Coupon.objects.create(
            project=self.project, code="B2G1", discount_type=DiscountType.BOGO,
            value=Decimal("0"), buy_quantity=2, get_quantity=1, get_discount_percent=Decimal("100"),
        )
        discount = apply_to_order(order=self.order, code="B2G1")
        self.order.refresh_from_db()
        self.assertEqual(discount, Decimal("100.00"))
        self.assertEqual(self.order.discount_total, Decimal("100.00"))
        self.assertEqual(self.order.grand_total, Decimal("200.00"))
