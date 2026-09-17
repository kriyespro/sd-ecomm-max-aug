"""Bulk restock on Inventory — was one "Adjust" click per row. Adds a
shared quantity to every selected item (one shipment batch), looping
inv.receive_stock() per item since each call locks the row and appends a
real StockMovement ledger entry (not something a raw bulk .update() can
do)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.catalog.models import Product
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.inventory.models import InventoryItem, StockMovement, Warehouse
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class InventoryBulkReceiveTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="InvCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("iowner", "iowner@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.wh = Warehouse.objects.create(project=self.project, name="Main")
        self.p1 = Product.objects.create(project=self.project, title="Shirt", slug="shirt-i", price=Decimal("500"), status="active")
        self.p2 = Product.objects.create(project=self.project, title="Pants", slug="pants-i", price=Decimal("800"), status="active")
        self.i1 = InventoryItem.objects.create(warehouse=self.wh, product=self.p1, quantity=10)
        self.i2 = InventoryItem.objects.create(warehouse=self.wh, product=self.p2, quantity=5)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_bulk_receive_adds_same_qty_to_each_selected(self):
        resp = self.client.post("/admin/inventory/bulk-receive/", {
            "quantity": "20", "pks": [self.i1.pk, self.i2.pk],
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.i1.refresh_from_db()
        self.i2.refresh_from_db()
        self.assertEqual(self.i1.quantity, 30)
        self.assertEqual(self.i2.quantity, 25)

    def test_writes_a_stock_movement_per_item(self):
        self.client.post("/admin/inventory/bulk-receive/", {
            "quantity": "20", "pks": [self.i1.pk, self.i2.pk],
        })
        self.assertEqual(
            StockMovement.objects.filter(item__in=[self.i1, self.i2], reason="purchase").count(), 2,
        )

    def test_zero_or_blank_quantity_is_rejected(self):
        resp = self.client.post("/admin/inventory/bulk-receive/", {
            "quantity": "0", "pks": [self.i1.pk],
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.i1.refresh_from_db()
        self.assertEqual(self.i1.quantity, 10)

    def test_no_selection_is_a_no_op(self):
        resp = self.client.post("/admin/inventory/bulk-receive/", {"quantity": "5"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.i1.refresh_from_db()
        self.assertEqual(self.i1.quantity, 10)

    def test_cannot_touch_another_projects_item(self):
        other = Project.objects.create(name="OtherInvCo", status="active")
        other_wh = Warehouse.objects.create(project=other, name="Elsewhere")
        other_product = Product.objects.create(project=other, title="X", slug="x-inv", price=Decimal("1"), status="active")
        foreign = InventoryItem.objects.create(warehouse=other_wh, product=other_product, quantity=1)
        self.client.post("/admin/inventory/bulk-receive/", {
            "quantity": "10", "pks": [foreign.pk],
        })
        foreign.refresh_from_db()
        self.assertEqual(foreign.quantity, 1)

    def test_list_page_renders_checkboxes_and_toolbar(self):
        resp = self.client.get("/admin/inventory/")
        self.assertContains(resp, 'name="pks"')
        self.assertContains(resp, 'id="bulk-select-all"')
        self.assertContains(resp, "Restock selected")

    def test_checkboxes_have_accessible_names(self):
        resp = self.client.get("/admin/inventory/")
        self.assertContains(resp, 'aria-label="Select all inventory items"')
        self.assertContains(resp, 'aria-label="Select Shirt"')
