"""Demo-content actions take an automatic safety backup first (restorable from
Store profile -> Demo content), and the global demo bar disappears once the
store has a custom domain."""

import shutil
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Membership, StoreRole
from apps.catalog.models import Product
from apps.control import safety_backup, starter_content
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.control.models import StoreBackupSnapshot
from apps.control.store_backup import BackupError
from apps.orders.models import Order
from apps.projects.models import Domain, Project

User = get_user_model()
_MEDIA = tempfile.mkdtemp(prefix="demo-safety-")


def tearDownModule():
    shutil.rmtree(_MEDIA, ignore_errors=True)


@override_settings(ALLOWED_HOSTS=["*"], MEDIA_ROOT=_MEDIA,
                   PLATFORM_BASE_DOMAIN="shopinaday.com", PLATFORM_HOSTS=["shopinaday.com"])
class DemoSafetyBackupTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="SafeCo", status="active", feature_flags={"onboarded": True})
        self.owner = User.objects.create_user("sb-owner", "sb@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def _mine(self):
        return Product.objects.create(project=self.project, title="My lamp", slug="my-lamp",
                                      price=Decimal("99"), status="active")

    # --- header bar vs custom domain ---------------------------------------
    def _seed_flag(self):
        self.project.feature_flags = {"onboarded": True, "demo_seeded": True}
        self.project.save(update_fields=["feature_flags"])

    def test_demo_bar_and_header_button_show_without_custom_domain(self):
        self._seed_flag()
        Domain.objects.create(project=self.project, host="safeco.shopinaday.com",
                              is_verified=True, is_primary=True)
        resp = self.client.get("/admin/products/")
        self.assertContains(resp, "Remove demo content")
        self.assertContains(resp, "swap the grey")

    def test_demo_bar_and_header_button_hidden_with_custom_domain(self):
        self._seed_flag()
        Domain.objects.create(project=self.project, host="safeco.in", is_verified=True, is_primary=True)
        resp = self.client.get("/admin/products/")
        self.assertNotContains(resp, "Remove demo content")
        self.assertNotContains(resp, "swap the grey")
        self.assertNotContains(resp, "Import demo content")  # still seeded, so no import link either
        # ...but the Store profile card keeps the actions
        card = self.client.get("/admin/cms/store-profile/")
        self.assertContains(card, "Remove demo</button>")
        self.assertContains(card, "Reset &amp; import demo")

    # --- backup before remove ----------------------------------------------
    def test_remove_demo_takes_backup_first(self):
        starter_content.seed_starter_content(self.project)
        before = Product.objects.filter(project=self.project).count()
        self.assertGreater(before, 0)
        r = self.client.post(reverse("control:demo_remove"), {"next": "/admin/cms/store-profile/"},
                             follow=True)
        snap = StoreBackupSnapshot.objects.get(project=self.project)
        self.assertEqual((snap.label, snap.includes_orders), ("Before removing demo content", False))
        self.assertContains(r, "A backup was saved first")
        self.assertEqual(Product.objects.filter(project=self.project).count(), 0)
        # restore brings the demo catalogue back
        self.client.post(reverse("control:demo_restore", kwargs={"pk": snap.pk}))
        self.assertEqual(Product.objects.filter(project=self.project).count(), before)

    def test_reset_backs_up_then_restore_returns_my_products_and_keeps_orders(self):
        self._mine()
        order = Order.objects.create(
            project=self.project, number="SB-1", email="b@t.test", subtotal=Decimal("1"),
            discount_total=Decimal("0"), tax_total=Decimal("0"), shipping_total=Decimal("0"),
            grand_total=Decimal("1"), status="confirmed", payment_status="paid",
            fulfillment_status="unfulfilled")
        r = self.client.post(reverse("control:demo_import"), {"confirm": "DELETE"}, follow=True)
        self.assertContains(r, "backed up")
        self.assertFalse(Product.objects.filter(project=self.project, slug="my-lamp").exists())
        snap = safety_backup.latest(self.project)
        self.assertEqual(snap.label, "Before demo reset")
        self.client.post(reverse("control:demo_restore", kwargs={"pk": snap.pk}))
        self.assertTrue(Product.objects.filter(project=self.project, slug="my-lamp").exists())
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())

    def test_owner_snapshot_restore_never_wipes_orders_for_safety_snapshot(self):
        self._mine()
        order = Order.objects.create(
            project=self.project, number="SB-2", email="b@t.test", subtotal=Decimal("1"),
            discount_total=Decimal("0"), tax_total=Decimal("0"), shipping_total=Decimal("0"),
            grand_total=Decimal("1"), status="confirmed", payment_status="paid",
            fulfillment_status="unfulfilled")
        snap = safety_backup.take(self.project, "Before demo reset")
        self.client.post(reverse("control:owner_backup_snapshot_restore", kwargs={"pk": snap.pk}))
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())

    def test_wrong_confirm_changes_nothing_and_makes_no_backup(self):
        self._mine()
        self.client.post(reverse("control:demo_import"), {"confirm": "nope"})
        self.assertTrue(Product.objects.filter(project=self.project, slug="my-lamp").exists())
        self.assertFalse(StoreBackupSnapshot.objects.exists())

    def test_failed_backup_aborts_the_wipe(self):
        self._mine()
        with patch("apps.control.safety_backup.take", side_effect=BackupError("too big")):
            r = self.client.post(reverse("control:demo_import"), {"confirm": "DELETE"}, follow=True)
        self.assertContains(r, "nothing was changed")
        self.assertTrue(Product.objects.filter(project=self.project, slug="my-lamp").exists())
        with patch("apps.control.safety_backup.take", side_effect=OSError("disk full")):
            self.client.post(reverse("control:demo_import"), {"confirm": "DELETE"})
        self.assertTrue(Product.objects.filter(project=self.project, slug="my-lamp").exists())

    def test_profile_card_lists_backup_with_restore_button(self):
        snap = safety_backup.take(self.project, "Before demo reset")
        resp = self.client.get("/admin/cms/store-profile/")
        self.assertContains(resp, "Before demo reset")
        self.assertContains(resp, f"/admin/cms/demo-content/restore/{snap.pk}/")
        self.assertContains(resp, "Safe to try")

    def test_restore_rejects_other_stores_and_full_snapshots(self):
        other = Project.objects.create(name="Other", status="active")
        theirs = safety_backup.take(other, "Before demo reset")
        self.assertEqual(self.client.post(
            reverse("control:demo_restore", kwargs={"pk": theirs.pk})).status_code, 404)
        full = StoreBackupSnapshot.objects.create(project=self.project, label="", includes_orders=True)
        self.assertEqual(self.client.post(
            reverse("control:demo_restore", kwargs={"pk": full.pk})).status_code, 404)

    def test_only_newest_three_safety_backups_kept_and_daily_prune_spares_them(self):
        for _ in range(5):
            safety_backup.take(self.project, "Before demo reset")
        self.assertEqual(StoreBackupSnapshot.objects.filter(project=self.project).count(), safety_backup.KEEP)
