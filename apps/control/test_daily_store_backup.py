"""Daily automatic store backups (apps.control.tasks.daily_store_backup_task
+ apps.control.models.StoreBackupSnapshot): one full backup per active
store every day, 7-day rolling retention, owner-facing one-click restore
on /admin/backup/. Same engine as the owner's manual backup
(apps.control.store_backup) -- these are just system-generated instead of
button-clicked, and always include_sensitive=True (orders/customers/
payments), never shown to a DGC without a real store membership."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.billing import services as billing_svc
from apps.catalog.models import Product
from apps.control import tasks as control_tasks
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.control.models import RETENTION_DAYS, StoreBackupSnapshot
from apps.control.store_backup import MAX_PRODUCTS
from apps.projects.models import Project

User = get_user_model()


class DailyStoreBackupTaskTests(TestCase):
    def setUp(self):
        self.active = Project.objects.create(name="ActiveCo", status=Project.Status.ACTIVE)
        Product.objects.create(
            project=self.active, title="Widget", slug="widget",
            price=Decimal("100"), status="active",
        )
        self.draft = Project.objects.create(name="DraftCo", status=Project.Status.DRAFT)
        self.archived = Project.objects.create(name="ArchivedCo", status=Project.Status.ARCHIVED)

    def test_creates_one_snapshot_per_active_store_only(self):
        result = control_tasks.daily_store_backup_task()
        self.assertEqual(result["created"], 1)
        self.assertEqual(StoreBackupSnapshot.objects.filter(project=self.active).count(), 1)
        self.assertFalse(StoreBackupSnapshot.objects.filter(project=self.draft).exists())
        self.assertFalse(StoreBackupSnapshot.objects.filter(project=self.archived).exists())

    def test_snapshot_records_size(self):
        control_tasks.daily_store_backup_task()
        snap = StoreBackupSnapshot.objects.get(project=self.active)
        self.assertGreater(snap.size_bytes, 0)
        self.assertTrue(snap.archive.name)

    def test_snapshot_survives_reading_back_the_archive(self):
        control_tasks.daily_store_backup_task()
        snap = StoreBackupSnapshot.objects.get(project=self.active)
        blob = snap.archive.read()
        self.assertTrue(blob.startswith(b"PK"))

    def test_old_snapshots_are_pruned(self):
        control_tasks.daily_store_backup_task()
        old = StoreBackupSnapshot.objects.get(project=self.active)
        StoreBackupSnapshot.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=RETENTION_DAYS + 1),
        )
        control_tasks.daily_store_backup_task()
        self.assertEqual(StoreBackupSnapshot.objects.filter(project=self.active).count(), 1)
        self.assertFalse(StoreBackupSnapshot.objects.filter(pk=old.pk).exists())

    def test_recent_snapshots_are_kept(self):
        control_tasks.daily_store_backup_task()
        control_tasks.daily_store_backup_task()
        self.assertEqual(StoreBackupSnapshot.objects.filter(project=self.active).count(), 2)

    def test_a_store_over_the_product_cap_does_not_block_the_others(self):
        Product.objects.bulk_create([
            Product(project=self.active, title=f"P{i}", slug=f"p{i}",
                    price=Decimal("1"), status="active")
            for i in range(MAX_PRODUCTS)
        ])
        other = Project.objects.create(name="FineCo", status=Project.Status.ACTIVE)
        Product.objects.create(
            project=other, title="OK", slug="ok", price=Decimal("1"), status="active",
        )
        result = control_tasks.daily_store_backup_task()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["created"], 1)
        self.assertTrue(StoreBackupSnapshot.objects.filter(project=other).exists())
        self.assertFalse(StoreBackupSnapshot.objects.filter(project=self.active).exists())


@override_settings(ALLOWED_HOSTS=["*"])
class BackupPageAutoSnapshotsTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="SnapScreenCo", status=Project.Status.ACTIVE,
            feature_flags={"onboarded": True},
        )
        Product.objects.create(
            project=self.project, title="Widget", slug="widget",
            price=Decimal("100"), status="active",
        )
        billing_svc.ensure_subscription(self.project)
        self.owner = User.objects.create_user("sso", "sso@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        control_tasks.daily_store_backup_task()
        self.snap = StoreBackupSnapshot.objects.get(project=self.project)

    def _login(self, user):
        self.client.force_login(user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_owner_sees_the_panel_and_can_download(self):
        self._login(self.owner)
        body = self.client.get("/admin/backup/").content.decode()
        self.assertIn("Automatic backups", body)
        resp = self.client.get(f"/admin/backup/snapshots/{self.snap.pk}/download/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/zip")

    def test_owner_can_restore_a_snapshot(self):
        Product.objects.filter(project=self.project).delete()
        self._login(self.owner)
        resp = self.client.post(f"/admin/backup/snapshots/{self.snap.pk}/restore/", follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Product.objects.filter(project=self.project, slug="widget").exists())

    def test_dgc_without_membership_does_not_see_the_panel(self):
        dgc = User.objects.create_user("ssd", "ssd@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=dgc).update(platform_role=PlatformRole.MANAGER)
        dgc = User.objects.get(pk=dgc.pk)
        self.project.subscription.manager = dgc
        self.project.subscription.save(update_fields=["manager"])
        self._login(dgc)
        body = self.client.get("/admin/backup/").content.decode()
        self.assertNotIn("Automatic backups", body)

    def test_dgc_without_membership_cannot_download_or_restore_a_snapshot(self):
        dgc = User.objects.create_user("ssd2", "ssd2@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=dgc).update(platform_role=PlatformRole.MANAGER)
        dgc = User.objects.get(pk=dgc.pk)
        self.project.subscription.manager = dgc
        self.project.subscription.save(update_fields=["manager"])
        self._login(dgc)
        self.assertEqual(
            self.client.get(f"/admin/backup/snapshots/{self.snap.pk}/download/").status_code, 404,
        )
        self.assertEqual(
            self.client.post(f"/admin/backup/snapshots/{self.snap.pk}/restore/").status_code, 404,
        )

    def test_cannot_reach_another_stores_snapshot(self):
        other = Project.objects.create(
            name="OtherSnapCo", status=Project.Status.ACTIVE, feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(other)
        other_owner = User.objects.create_user("oso", "oso@t.test", "pw", is_staff=True)
        Membership.objects.create(project=other, user=other_owner, role=StoreRole.OWNER)
        self.client.force_login(other_owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = other.pk
        s.save()
        self.assertEqual(
            self.client.get(f"/admin/backup/snapshots/{self.snap.pk}/download/").status_code, 404,
        )
