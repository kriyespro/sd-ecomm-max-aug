"""Events.INVENTORY_LOW was emitted on every stock-crossing-into-low, but
never mapped to a notification — the owner never actually heard about it.
Wired here: apps/notifications/signals.py._MAP + owner-email resolution."""

from decimal import Decimal
from unittest import mock

from django.test import TestCase

from apps.accounts.models import Membership, StoreRole
from apps.catalog.models import Product
from apps.cms.models import StoreProfile
from apps.inventory import services as inv
from apps.inventory.models import InventoryItem, Warehouse
from apps.notifications.models import Event, NotificationLog, SendStatus
from apps.projects.models import Project
from apps.projects.services import owner_notification_email, owner_notification_phone


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


class OwnerNotificationPhoneTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="OwnerPhoneCo", status="active")

    def test_falls_back_to_owner_membership_phone(self):
        from django.contrib.auth import get_user_model

        owner = get_user_model().objects.create_user(
            "op", "op@t.test", "pw", is_staff=True,
        )
        owner.profile.phone = "919999999999"
        owner.profile.save(update_fields=["phone"])
        Membership.objects.create(project=self.project, user=owner, role=StoreRole.OWNER)
        self.assertEqual(owner_notification_phone(self.project), "919999999999")

    def test_store_profile_support_phone_wins_over_owner_membership(self):
        StoreProfile.objects.create(project=self.project, support_phone="918888888888")
        self.assertEqual(owner_notification_phone(self.project), "918888888888")

    def test_explicit_override_wins_over_everything(self):
        self.project.notification_config = {"owner_phone": "917777777777"}
        self.project.save(update_fields=["notification_config"])
        StoreProfile.objects.create(project=self.project, support_phone="918888888888")
        self.assertEqual(owner_notification_phone(self.project), "917777777777")

    def test_no_phone_anywhere_returns_blank_not_an_error(self):
        self.assertEqual(owner_notification_phone(self.project), "")


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

    def test_crossing_into_low_also_whatsapps_the_owner_when_phone_and_account_exist(self):
        from apps.whatsapp.models import WhatsAppAccount

        StoreProfile.objects.filter(project=self.project).update(support_phone="919999999999")
        WhatsAppAccount.objects.create(
            project=self.project, phone_number_id="PN1", waba_id="W1",
            access_token="tok", is_active=True,
        )
        item = InventoryItem.objects.create(
            warehouse=self.warehouse, product=self.product,
            quantity=10, low_stock_threshold=5,
        )
        with mock.patch("apps.whatsapp.tasks.send_whatsapp_task.delay") as delay, \
             self.captureOnCommitCallbacks(execute=True):
            inv.reserve(item=item, quantity=6)
        delay.assert_called_once()
        args = delay.call_args.args
        self.assertEqual(args[0], self.project.id)
        self.assertEqual(args[1], Event.LOW_STOCK_ALERT)
        self.assertEqual(args[2], "919999999999")

    def test_crossing_into_low_skips_whatsapp_without_a_phone(self):
        from apps.whatsapp.models import WhatsAppAccount

        WhatsAppAccount.objects.create(
            project=self.project, phone_number_id="PN2", waba_id="W2",
            access_token="tok", is_active=True,
        )
        item = InventoryItem.objects.create(
            warehouse=self.warehouse, product=self.product,
            quantity=10, low_stock_threshold=5,
        )
        with mock.patch("apps.whatsapp.tasks.send_whatsapp_task.delay") as delay, \
             self.captureOnCommitCallbacks(execute=True):
            inv.reserve(item=item, quantity=6)
        delay.assert_not_called()
