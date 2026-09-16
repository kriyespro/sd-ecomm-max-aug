"""Events.INVENTORY_LOW was emitted on every stock-crossing-into-low, but
never mapped to a notification — the owner never actually heard about it.
Wired here: apps/notifications/signals.py._MAP + owner-email resolution."""

from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import Membership, StoreRole
from apps.catalog.models import Product
from apps.cms.models import StoreProfile
from apps.inventory import services as inv
from apps.inventory.models import InventoryItem, Warehouse
from apps.notifications.models import Event, NotificationLog, SendStatus
from apps.projects.models import Project
from apps.projects.services import owner_notification_email


class OwnerNotificationEmailTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="OwnerEmailCo", status="active")

    def test_falls_back_to_owner_membership_when_nothing_else_set(self):
        from django.contrib.auth import get_user_model

        owner = get_user_model().objects.create_user(
            "o", "owner@t.test", "pw", is_staff=True,
        )
        Membership.objects.create(project=self.project, user=owner, role=StoreRole.OWNER)
        self.assertEqual(owner_notification_email(self.project), "owner@t.test")

    def test_store_profile_support_email_wins_over_owner_membership(self):
        from django.contrib.auth import get_user_model

        owner = get_user_model().objects.create_user(
            "o2", "owner2@t.test", "pw", is_staff=True,
        )
        Membership.objects.create(project=self.project, user=owner, role=StoreRole.OWNER)
        StoreProfile.objects.create(project=self.project, support_email="support@brand.test")
        self.assertEqual(owner_notification_email(self.project), "support@brand.test")

    def test_explicit_override_wins_over_everything(self):
        self.project.notification_config = {"owner_email": "override@brand.test"}
        self.project.save(update_fields=["notification_config"])
        StoreProfile.objects.create(project=self.project, support_email="support@brand.test")
        self.assertEqual(owner_notification_email(self.project), "override@brand.test")

    def test_no_address_anywhere_returns_blank_not_an_error(self):
        self.assertEqual(owner_notification_email(self.project), "")


class LowStockAlertWiringTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="LowStockCo", status="active")
        self.warehouse = Warehouse.objects.create(project=self.project, name="Main", is_default=True)
        self.product = Product.objects.create(
            project=self.project, title="Widget", price=Decimal("100"),
        )
        StoreProfile.objects.create(project=self.project, support_email="owner@lowstock.test")

    def test_crossing_into_low_sends_the_owner_an_email(self):
        item = InventoryItem.objects.create(
            warehouse=self.warehouse, product=self.product,
            quantity=10, low_stock_threshold=5,
        )
        with self.captureOnCommitCallbacks(execute=True):
            inv.reserve(item=item, quantity=6)  # available 4 < threshold 5 — crosses now
        item.refresh_from_db()
        self.assertTrue(item.is_low)

        log = NotificationLog.objects.filter(
            project=self.project, event=Event.LOW_STOCK_ALERT,
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.to_address, "owner@lowstock.test")
        self.assertEqual(log.status, SendStatus.SENT)
        self.assertIn("Widget", log.body)

    def test_already_low_does_not_re_alert_on_every_movement(self):
        item = InventoryItem.objects.create(
            warehouse=self.warehouse, product=self.product,
            quantity=10, low_stock_threshold=5,
        )
        with self.captureOnCommitCallbacks(execute=True):
            inv.reserve(item=item, quantity=6)  # first crossing — 1 alert
            inv.reserve(item=item, quantity=1)  # still low, not a new crossing

        self.assertEqual(
            NotificationLog.objects.filter(
                project=self.project, event=Event.LOW_STOCK_ALERT,
            ).count(),
            1,
        )

    def test_no_owner_email_anywhere_skips_cleanly_no_crash(self):
        StoreProfile.objects.filter(project=self.project).delete()
        item = InventoryItem.objects.create(
            warehouse=self.warehouse, product=self.product,
            quantity=10, low_stock_threshold=5,
        )
        with self.captureOnCommitCallbacks(execute=True):
            inv.reserve(item=item, quantity=6)  # must not raise
        log = NotificationLog.objects.filter(
            project=self.project, event=Event.LOW_STOCK_ALERT,
        ).first()
        self.assertEqual(log.status, SendStatus.SKIPPED)
