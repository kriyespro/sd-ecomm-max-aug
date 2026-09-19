"""Backup/restore's opt-in "sensitive" half (apps.control.store_backup
_SENSITIVE_REGISTRY): a store's own customers, orders, order items, order
status history, payments and refunds -- only ever bundled when a caller
passes ``include_sensitive=True``, and even then only actually restored
when the archive's own ``source_project_id`` matches the target store, so
one store's transaction history can never land inside another store.

apps/control/backup_views.py wires this up so the store's real OWNER (or a
platform admin) gets it on /admin/backup/, while a DGC reaching that same
screen via the subscription-manager bypass (apps.accounts.permissions
.dgc_without_membership) never does -- apps/control/test_store_backup.py's
OwnerBackupScreenTests already covers the plain-membership case; this file
is entirely about the sensitive half."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.billing import services as billing_svc
from apps.billing.models import Plan
from apps.catalog.models import Product
from apps.control import store_backup
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.customers.models import Customer, CustomerAddress, CustomerGroup
from apps.orders.models import Order, OrderItem, OrderStatusEvent
from apps.payments.models import Payment, Refund
from apps.projects.models import Project

User = get_user_model()


def _seed_transaction_data(project, *, user=None):
    group = CustomerGroup.objects.create(project=project, name="VIP", discount_percent=Decimal("10"))
    customer = Customer.objects.create(
        project=project, user=user, email="buyer@t.test", first_name="Bee", last_name="Yer",
        group=group, orders_count=1, total_spent=Decimal("500"),
    )
    CustomerAddress.objects.create(
        customer=customer, name="Bee Yer", line1="1 MG Road", city="Pune",
        postal_code="411001", is_default_shipping=True,
    )
    product = Product.objects.create(
        project=project, title="Widget", slug="widget", price=Decimal("500"), status="active",
    )
    order = Order.objects.create(
        project=project, number="SB-1", email="buyer@t.test", customer=customer,
        user=user, status="confirmed", grand_total=Decimal("500"),
    )
    OrderItem.objects.create(
        order=order, product=product, product_title="Widget", unit_price=Decimal("500"),
        quantity=1, line_total=Decimal("500"),
    )
    OrderStatusEvent.objects.create(order=order, kind="status", to_value="confirmed", actor=user)
    payment = Payment.objects.create(
        project=project, order=order, provider="razorpay", status="paid",
        amount=Decimal("500"), provider_payment_id="pay_abc123",
    )
    Refund.objects.create(payment=payment, amount=Decimal("100"), status="processed", actor=user)
    return {"group": group, "customer": customer, "product": product, "order": order}


@override_settings(ALLOWED_HOSTS=["*"])
class DumpAndRestoreSensitiveTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="SensiCo", status="active")
        self.owner = User.objects.create_user("so", "so@t.test", "pw")
        _seed_transaction_data(self.project, user=self.owner)

    def test_dump_without_flag_never_includes_sensitive_data(self):
        blob = store_backup.dump_store(self.project)
        import io
        import json
        import zipfile

        zf = zipfile.ZipFile(io.BytesIO(blob))
        manifest = json.loads(zf.read("manifest.json"))
        self.assertFalse(manifest["include_sensitive"])
        self.assertNotIn("customer", manifest["data"])
        self.assertNotIn("order", manifest["data"])

    def test_dump_with_flag_includes_sensitive_data(self):
        blob = store_backup.dump_store(self.project, include_sensitive=True)
        import io
        import json
        import zipfile

        zf = zipfile.ZipFile(io.BytesIO(blob))
        manifest = json.loads(zf.read("manifest.json"))
        self.assertTrue(manifest["include_sensitive"])
        self.assertEqual(manifest["source_project_id"], self.project.pk)
        self.assertEqual(len(manifest["data"]["customer"]), 1)
        self.assertEqual(len(manifest["data"]["order"]), 1)
        self.assertEqual(len(manifest["data"]["payment"]), 1)
        self.assertEqual(manifest["data"]["payment"][0]["provider_payment_id"], "pay_abc123")

    def test_round_trip_restore_onto_the_same_store(self):
        blob = store_backup.dump_store(self.project, include_sensitive=True)
        Order.objects.filter(project=self.project).delete()
        Customer.objects.filter(project=self.project).delete()

        counts = store_backup.restore_store(
            self.project, blob, actor=self.owner, include_sensitive=True,
        )
        self.assertEqual(counts["order"], 1)
        self.assertEqual(counts["customer"], 1)
        self.assertEqual(counts["payment"], 1)
        self.assertEqual(counts["refund"], 1)

        order = Order.objects.get(project=self.project, number="SB-1")
        self.assertEqual(order.customer.email, "buyer@t.test")
        self.assertEqual(order.customer.group.name, "VIP")
        self.assertEqual(order.items.first().product.slug, "widget")
        payment = order.payments.get()
        self.assertEqual(payment.provider_payment_id, "pay_abc123")
        self.assertEqual(payment.refunds.get().amount, Decimal("100"))
        self.assertEqual(order.customer.addresses.get().city, "Pune")

        # Never remapped to a live account -- always nulled, not relinked.
        self.assertIsNone(order.user_id)
        self.assertIsNone(order.customer.user_id)

    def test_restore_without_flag_ignores_sensitive_data_even_if_present(self):
        blob = store_backup.dump_store(self.project, include_sensitive=True)
        counts = store_backup.restore_store(self.project, blob, actor=self.owner)
        self.assertNotIn("order", counts)
        self.assertNotIn("customer", counts)
        # the pre-existing order/customer this store already had are untouched
        self.assertTrue(Order.objects.filter(project=self.project, number="SB-1").exists())
        self.assertTrue(Customer.objects.filter(project=self.project, email="buyer@t.test").exists())

    def test_restoring_a_different_stores_sensitive_backup_is_rejected(self):
        other = Project.objects.create(name="OtherCo", status="active")
        _seed_transaction_data(other)
        other_blob = store_backup.dump_store(other, include_sensitive=True)

        with self.assertRaises(store_backup.BackupError):
            store_backup.restore_store(
                self.project, other_blob, actor=self.owner, include_sensitive=True,
            )

        # nothing changed -- the whole transaction rolled back, including
        # the catalog/CMS half that would otherwise have been safe to apply
        self.assertTrue(Order.objects.filter(project=self.project, number="SB-1").exists())
        self.assertTrue(Product.objects.filter(project=self.project, slug="widget").exists())

    def test_order_cap_is_enforced_on_dump(self):
        with patch.object(store_backup, "MAX_ORDERS", 0):
            with self.assertRaises(store_backup.BackupError):
                store_backup.dump_store(self.project, include_sensitive=True)

    def test_order_cap_is_enforced_on_restore(self):
        blob = store_backup.dump_store(self.project, include_sensitive=True)
        with patch.object(store_backup, "MAX_ORDERS", 0):
            with self.assertRaises(store_backup.BackupError):
                store_backup.restore_store(
                    self.project, blob, actor=self.owner, include_sensitive=True,
                )


@override_settings(ALLOWED_HOSTS=["*"])
class OwnerBackupScreenSensitiveTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="ScreenCo", status="active", feature_flags={"onboarded": True},
        )
        sub = billing_svc.ensure_subscription(self.project)  # signal already made one, on Basic
        sub.plan = Plan.objects.get(code="growth")  # full backup is a Growth/Pro feature
        sub.save(update_fields=["plan"])
        self.owner = User.objects.create_user("po", "po@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        _seed_transaction_data(self.project, user=self.owner)

    def _login(self, user):
        self.client.force_login(user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_real_owner_download_includes_sensitive_data(self):
        self._login(self.owner)
        resp = self.client.get("/admin/backup/download/")
        import io
        import json
        import zipfile

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        manifest = json.loads(zf.read("manifest.json"))
        self.assertTrue(manifest["include_sensitive"])
        self.assertEqual(len(manifest["data"]["order"]), 1)

    def test_real_owner_restore_replaces_own_orders_and_customers(self):
        self._login(self.owner)
        blob = self.client.get("/admin/backup/download/").content
        Order.objects.filter(project=self.project).update(number="CHANGED")

        resp = self.client.post("/admin/backup/restore/", {
            "confirm_name": "ScreenCo",
            "backup": SimpleUploadedFile("b.zip", blob, "application/zip"),
        }, follow=True)
        self.assertContains(resp, "orders")
        self.assertTrue(Order.objects.filter(project=self.project, number="SB-1").exists())

    def test_dgc_without_membership_download_excludes_sensitive_data(self):
        dgc = User.objects.create_user("pd", "pd@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=dgc).update(platform_role=PlatformRole.MANAGER)
        dgc = User.objects.get(pk=dgc.pk)
        self.project.subscription.manager = dgc
        self.project.subscription.save(update_fields=["manager"])
        self._login(dgc)

        resp = self.client.get("/admin/backup/download/")
        import io
        import json
        import zipfile

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        manifest = json.loads(zf.read("manifest.json"))
        self.assertFalse(manifest["include_sensitive"])
        self.assertNotIn("order", manifest["data"])
        self.assertNotIn("customer", manifest["data"])

    def test_dgc_without_membership_restore_never_touches_orders_or_customers(self):
        dgc = User.objects.create_user("pd2", "pd2@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=dgc).update(platform_role=PlatformRole.MANAGER)
        dgc = User.objects.get(pk=dgc.pk)
        self.project.subscription.manager = dgc
        self.project.subscription.save(update_fields=["manager"])

        # A real owner-made backup of this same store -- DOES have sensitive
        # data in it. The DGC uploading it must still not get it restored.
        self._login(self.owner)
        full_blob = self.client.get("/admin/backup/download/").content

        self._login(dgc)
        Order.objects.filter(project=self.project).update(number="STILL-HERE")
        resp = self.client.post("/admin/backup/restore/", {
            "confirm_name": "ScreenCo",
            "backup": SimpleUploadedFile("b.zip", full_blob, "application/zip"),
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Order.objects.filter(project=self.project, number="STILL-HERE").exists())
        self.assertFalse(Order.objects.filter(project=self.project, number="SB-1").exists())

    def test_cross_store_sensitive_backup_upload_is_rejected_with_clear_message(self):
        other = Project.objects.create(
            name="OtherScreenCo", status="active", feature_flags={"onboarded": True},
        )
        other_sub = billing_svc.ensure_subscription(other)
        other_sub.plan = Plan.objects.get(code="growth")
        other_sub.save(update_fields=["plan"])
        other_owner = User.objects.create_user("oo", "oo@t.test", "pw", is_staff=True)
        Membership.objects.create(project=other, user=other_owner, role=StoreRole.OWNER)
        _seed_transaction_data(other, user=other_owner)
        self.client.force_login(other_owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = other.pk
        s.save()
        other_blob = self.client.get("/admin/backup/download/").content

        self._login(self.owner)
        resp = self.client.post("/admin/backup/restore/", {
            "confirm_name": "ScreenCo",
            "backup": SimpleUploadedFile("other.zip", other_blob, "application/zip"),
        }, follow=True)
        self.assertContains(resp, "different store")
        self.assertTrue(Order.objects.filter(project=self.project, number="SB-1").exists())
