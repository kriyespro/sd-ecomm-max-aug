from decimal import Decimal

from django.test import TestCase

from apps.analytics.services import today_dashboard
from apps.inventory.models import InventoryItem, Warehouse
from apps.orders.models import Order
from apps.projects.models import Project


def _order(project, number, **kw):
    defaults = dict(
        email="b@t.test", subtotal=Decimal("0"), discount_total=Decimal("0"),
        tax_total=Decimal("0"), shipping_total=Decimal("0"), grand_total=Decimal("500"),
        status="confirmed", payment_status="paid", fulfillment_status="unfulfilled",
    )
    defaults.update(kw)
    return Order.objects.create(project=project, number=number, **defaults)


class TodayDashboardTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Shop", status="active", currency="INR")

    def test_needs_action_counts_unfulfilled_paid_orders_only(self):
        _order(self.project, "A1", status="confirmed", fulfillment_status="unfulfilled")
        _order(self.project, "A2", status="processing", fulfillment_status="partial")
        _order(self.project, "A3", status="delivered", fulfillment_status="fulfilled")  # done
        _order(self.project, "A4", status="cancelled", fulfillment_status="unfulfilled")  # cancelled
        _order(self.project, "A5", status="confirmed", fulfillment_status="unfulfilled",
              is_archived=True)  # archived, excluded

        out = today_dashboard(self.project)
        self.assertEqual(out["needs_action_count"], 2)
        numbers = {o.number for o in out["needs_action"]}
        self.assertEqual(numbers, {"A1", "A2"})

    def test_needs_action_list_capped_but_count_is_not(self):
        for i in range(12):
            _order(self.project, f"N{i}", status="confirmed", fulfillment_status="unfulfilled")
        out = today_dashboard(self.project)
        self.assertEqual(out["needs_action_count"], 12)
        self.assertEqual(len(out["needs_action"]), 8)

    def test_recent_orders_excludes_archived_orders_only(self):
        _order(self.project, "R1")
        _order(self.project, "R2", is_archived=True)
        out = today_dashboard(self.project)
        numbers = {o.number for o in out["recent_orders"]}
        self.assertIn("R1", numbers)
        self.assertNotIn("R2", numbers)

    def test_low_stock_items_surfaced(self):
        wh = Warehouse.objects.create(project=self.project, name="Main")
        from apps.catalog.models import Product

        product = Product.objects.create(project=self.project, title="Lamp", slug="lamp",
                                         price=Decimal("99"), status="active")
        InventoryItem.objects.create(warehouse=wh, product=product, quantity=1,
                                     reserved=0, low_stock_threshold=5)
        out = today_dashboard(self.project)
        self.assertEqual(len(out["low_stock_items"]), 1)
        self.assertEqual(out["low_stock_items"][0].product_id, product.pk)

    def test_still_has_the_underlying_dashboard_summary_keys(self):
        out = today_dashboard(self.project)
        for key in ("sales", "orders_by_status", "customers", "products", "revenue_series"):
            self.assertIn(key, out)
