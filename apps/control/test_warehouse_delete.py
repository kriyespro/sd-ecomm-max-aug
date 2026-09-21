"""Regression: deleting a warehouse that's ever been part of a stock
transfer used to 500 (InventoryTransfer.source/destination is on_delete=
PROTECT, same class of bug as the store-delete 500 fixed earlier -- see
WarehouseDeleteView.form_valid)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.catalog.models import Product
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.inventory.models import InventoryTransfer, Warehouse
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class WarehouseDeleteProtectedTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="WhDelCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("who", "who@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

        self.product = Product.objects.create(
            project=self.project, title="Lamp", slug="lamp", price="100",
        )
        self.wh1 = Warehouse.objects.create(project=self.project, name="Main", code="main")
        self.wh2 = Warehouse.objects.create(project=self.project, name="Backup", code="backup")

    def test_delete_warehouse_with_no_transfers_succeeds(self):
        pk = self.wh1.pk
        resp = self.client.post(f"/admin/warehouses/{pk}/delete/", follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Warehouse.objects.filter(pk=pk).exists())

    def test_delete_warehouse_with_a_transfer_shows_friendly_error_not_500(self):
        InventoryTransfer.objects.create(
            project=self.project, source=self.wh1, destination=self.wh2,
            product=self.product, quantity=1,
        )
        pk = self.wh1.pk
        resp = self.client.post(f"/admin/warehouses/{pk}/delete/", follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "still has a stock transfer on record")
        self.assertTrue(Warehouse.objects.filter(pk=pk).exists())
