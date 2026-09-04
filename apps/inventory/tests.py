from decimal import Decimal

from django.test import TestCase

from apps.catalog.models import Product
from apps.inventory import services as inv
from apps.inventory.models import InventoryItem, Warehouse
from apps.projects.models import Project


class ReserveStockTests(TestCase):
    """`reserve()` must hard-block once a stocked item's available quantity
    is exhausted, without disturbing the untracked (no InventoryItem row)
    case, which stays unlimited by design."""

    def setUp(self):
        self.project = Project.objects.create(name="StockCo", status="active")
        self.warehouse = Warehouse.objects.create(project=self.project, name="Main", is_default=True)
        self.product = Product.objects.create(project=self.project, title="Widget", price=Decimal("100"))

    def test_reserve_within_stock_succeeds(self):
        item = InventoryItem.objects.create(warehouse=self.warehouse, product=self.product, quantity=5)
        inv.reserve(item=item, quantity=3)
        item.refresh_from_db()
        self.assertEqual(item.reserved, 3)
        self.assertEqual(item.available, 2)

    def test_reserve_past_available_raises(self):
        item = InventoryItem.objects.create(warehouse=self.warehouse, product=self.product, quantity=2)
        inv.reserve(item=item, quantity=2)
        with self.assertRaises(inv.InsufficientStockError):
            inv.reserve(item=item, quantity=1)
        item.refresh_from_db()
        self.assertEqual(item.reserved, 2)  # the failed attempt left no partial change

    def test_release_then_consume_round_trip(self):
        item = InventoryItem.objects.create(warehouse=self.warehouse, product=self.product, quantity=5)
        inv.reserve(item=item, quantity=4)
        inv.release(item=item, quantity=1)
        item.refresh_from_db()
        self.assertEqual(item.reserved, 3)
        inv.consume_sale(item=item, quantity=3)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.reserved, 0)
