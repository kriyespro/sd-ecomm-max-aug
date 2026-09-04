"""place_order: stock enforcement, untracked-product regression guard, and
double-submission protection on the same cart."""

from decimal import Decimal

from django.test import TestCase

from apps.cart.models import Cart, CartItem
from apps.catalog.models import Product
from apps.inventory.models import InventoryItem, Warehouse
from apps.orders.services import OrderError, place_order
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
