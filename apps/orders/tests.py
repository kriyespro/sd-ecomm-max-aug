"""place_order: stock enforcement, untracked-product regression guard, and
double-submission protection on the same cart."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.cart.models import Cart, CartItem
from apps.catalog.models import Product
from apps.inventory.models import InventoryItem, Warehouse
from apps.orders.models import Order, OrderStatus
from apps.orders.services import OrderError, expire_stale_pending_orders, place_order
from apps.projects.models import Project


class PlaceOrderStockTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="OrderCo", status="active")
        self.warehouse = Warehouse.objects.create(project=self.project, name="Main", is_default=True)

    def _cart_for(self, product, quantity=1):
        cart = Cart.objects.create(project=self.project, is_active=True)
        CartItem.objects.create(cart=cart, product=product, quantity=quantity, unit_price=product.price)
        return cart

    def test_untracked_product_has_unlimited_stock(self):
        """No InventoryItem row at all -> never blocked, matches the
        storefront's own 'no row = in stock' convention."""
        product = Product.objects.create(project=self.project, title="Untracked", price=Decimal("50"))
        order = place_order(
            project=self.project, cart=self._cart_for(product),
            email="a@t.test", billing_address={}, shipping_address={"name": "A"},
        )
        self.assertEqual(order.items.count(), 1)
        self.assertFalse(InventoryItem.objects.filter(product=product).exists())

    def test_stocked_product_blocks_order_once_exhausted(self):
        product = Product.objects.create(project=self.project, title="Stocked", price=Decimal("50"))
        InventoryItem.objects.create(warehouse=self.warehouse, product=product, quantity=1)

        # First buyer takes the only unit.
        order = place_order(
            project=self.project, cart=self._cart_for(product),
            email="first@t.test", billing_address={}, shipping_address={"name": "First"},
        )
        self.assertEqual(order.items.count(), 1)

        # Second buyer is rejected, not oversold.
        with self.assertRaises(OrderError):
            place_order(
                project=self.project, cart=self._cart_for(product),
                email="second@t.test", billing_address={}, shipping_address={"name": "Second"},
            )
        item = InventoryItem.objects.get(product=product)
        self.assertEqual(item.reserved, 1)  # the rejected attempt reserved nothing

    def test_double_submit_same_cart_rejected(self):
        product = Product.objects.create(project=self.project, title="Doubled", price=Decimal("50"))
        cart = self._cart_for(product)
        order = place_order(
            project=self.project, cart=cart,
            email="x@t.test", billing_address={}, shipping_address={"name": "X"},
        )
        with self.assertRaises(OrderError):
            place_order(
                project=self.project, cart=cart,
                email="x@t.test", billing_address={}, shipping_address={"name": "X"},
            )
        from apps.orders.models import Order

        self.assertEqual(Order.objects.filter(project=self.project).count(), 1)
        self.assertEqual(order.pk, Order.objects.get().pk)


class ExpireStalePendingOrdersTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="ExpireCo", status="active")
        self.warehouse = Warehouse.objects.create(project=self.project, name="Main", is_default=True)
        self.product = Product.objects.create(project=self.project, title="Item", price=Decimal("100"))

    def _order(self, *, age_hours=0):
        cart = Cart.objects.create(project=self.project, is_active=True)
        CartItem.objects.create(cart=cart, product=self.product, quantity=1, unit_price=self.product.price)
        order = place_order(
            project=self.project, cart=cart,
            email="x@t.test", billing_address={}, shipping_address={"name": "X"},
        )
        if age_hours:
            Order.objects.filter(pk=order.pk).update(
                created_at=timezone.now() - timedelta(hours=age_hours)
            )
            order.refresh_from_db()
        return order

    def _attach_payment(self, order, *, provider="razorpay"):
        from apps.payments.models import Payment

        return Payment.objects.create(
            project=self.project, order=order, provider=provider,
            amount=order.grand_total, currency=order.currency,
        )

    def test_stale_gateway_order_is_cancelled_and_stock_released(self):
        item = InventoryItem.objects.create(warehouse=self.warehouse, product=self.product, quantity=5)
        order = self._order(age_hours=72)
        self._attach_payment(order, provider="razorpay")

        cancelled = expire_stale_pending_orders(older_than_hours=48)

        order.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual([o.pk for o in cancelled], [order.pk])
        self.assertEqual(order.status, OrderStatus.CANCELLED)
        self.assertEqual(item.reserved, 0)

    def test_recent_gateway_order_is_left_alone(self):
        order = self._order(age_hours=1)
        self._attach_payment(order, provider="razorpay")

        expire_stale_pending_orders(older_than_hours=48)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)

    def test_stale_cod_order_is_never_auto_cancelled(self):
        order = self._order(age_hours=72)
        self._attach_payment(order, provider="cod")

        expire_stale_pending_orders(older_than_hours=48)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)

    def test_order_with_no_payment_attempt_at_all_is_left_alone(self):
        # e.g. a plain PENDING order nobody has tried to pay for yet
        order = self._order(age_hours=72)
        expire_stale_pending_orders(older_than_hours=48)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)
