"""Daily automatic store backups (apps.control.tasks.daily_store_backup_task
+ apps.control.models.StoreBackupSnapshot): opt-in per store
(Project.feature_flags["auto_backup"], off by default, toggled from an
"Automatic backup" checkbox on /admin/backup/), one full backup per
opted-in active store per day, 7-day rolling retention, owner-facing
one-click restore. Same engine as the owner's manual backup
(apps.control.store_backup) -- these are just system-generated instead of
button-clicked, and always include_sensitive=True (orders/customers/
payments), never shown to a DGC without a real store membership.

Scheduled every 30 min from 12:00-5:30 AM IST (CELERY_TIMEZONE +
CELERY_BEAT_SCHEDULE) -- each run only picks up BACKUP_BATCH_SIZE stores
that haven't been backed up yet today, so a large store count spreads
across the window instead of spiking at midnight."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.billing import services as billing_svc
from apps.billing.models import Plan
from apps.catalog.models import Product
from apps.control import tasks as control_tasks
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.control.models import RETENTION_DAYS, StoreBackupSnapshot
from apps.control.store_backup import MAX_PRODUCTS
from apps.projects.models import Project

User = get_user_model()


def _active_project(name, *, auto_backup=True, plan_code="growth", **extra):
    """A store on a plan with allow_full_backup=True (growth/pro) by
    default -- the automatic backup task and the owner's full-backup access
    both require that, on top of the auto_backup opt-in flag itself.

    Project.objects.create() already fires a post_save signal that gives
    the project a trial Subscription on the default (Basic) plan before
    this function runs -- ensure_subscription() is idempotent and would
    silently ignore the plan= passed below since one already exists, so
    the plan is reassigned explicitly afterward (same fix store_services
    .create_store() already applies for the same reason)."""
    flags = extra.pop("feature_flags", {})
    flags["auto_backup"] = auto_backup
    project = Project.objects.create(
        name=name, status=Project.Status.ACTIVE, feature_flags=flags, **extra,
    )
    if plan_code:
        sub = billing_svc.ensure_subscription(project)
        sub.plan = Plan.objects.get(code=plan_code)
        sub.save(update_fields=["plan"])
    return project


class DailyStoreBackupTaskTests(TestCase):
    def setUp(self):
        self.active = _active_project("ActiveCo")
        Product.objects.create(
            project=self.active, title="Widget", slug="widget",
            price=Decimal("100"), status="active",
        )
        self.draft = Project.objects.create(
            name="DraftCo", status=Project.Status.DRAFT, feature_flags={"auto_backup": True},
        )
        self.archived = Project.objects.create(
            name="ArchivedCo", status=Project.Status.ARCHIVED, feature_flags={"auto_backup": True},
        )

    def test_creates_one_snapshot_per_opted_in_active_store_only(self):
        result = control_tasks.daily_store_backup_task()
        self.assertEqual(result["created"], 1)
        self.assertEqual(StoreBackupSnapshot.objects.filter(project=self.active).count(), 1)
        self.assertFalse(StoreBackupSnapshot.objects.filter(project=self.draft).exists())
        self.assertFalse(StoreBackupSnapshot.objects.filter(project=self.archived).exists())

    def test_store_without_auto_backup_enabled_is_skipped(self):
        opted_out = _active_project("OptedOutCo", auto_backup=False)
        control_tasks.daily_store_backup_task()
        self.assertFalse(StoreBackupSnapshot.objects.filter(project=opted_out).exists())

    def test_store_with_no_feature_flags_at_all_is_skipped(self):
        bare = Project.objects.create(name="BareCo", status=Project.Status.ACTIVE)
        control_tasks.daily_store_backup_task()
        self.assertFalse(StoreBackupSnapshot.objects.filter(project=bare).exists())

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

    def test_running_twice_in_the_same_day_does_not_duplicate(self):
        control_tasks.daily_store_backup_task()
        result = control_tasks.daily_store_backup_task()
        self.assertEqual(result["created"], 0)
        self.assertEqual(StoreBackupSnapshot.objects.filter(project=self.active).count(), 1)

    def test_old_snapshots_are_pruned(self):
        control_tasks.daily_store_backup_task()
        old = StoreBackupSnapshot.objects.get(project=self.active)
        StoreBackupSnapshot.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=RETENTION_DAYS + 1),
        )
        control_tasks.daily_store_backup_task()
        self.assertEqual(StoreBackupSnapshot.objects.filter(project=self.active).count(), 1)
        self.assertFalse(StoreBackupSnapshot.objects.filter(pk=old.pk).exists())

    def test_recent_snapshots_from_a_previous_day_are_kept_alongside_todays(self):
        control_tasks.daily_store_backup_task()
        yesterday = StoreBackupSnapshot.objects.get(project=self.active)
        StoreBackupSnapshot.objects.filter(pk=yesterday.pk).update(
            created_at=timezone.now() - timedelta(days=1),
        )
        control_tasks.daily_store_backup_task()
        self.assertEqual(StoreBackupSnapshot.objects.filter(project=self.active).count(), 2)

    def test_a_store_over_the_product_cap_does_not_block_the_others(self):
        Product.objects.bulk_create([
            Product(project=self.active, title=f"P{i}", slug=f"p{i}",
                    price=Decimal("1"), status="active")
            for i in range(MAX_PRODUCTS)
        ])
        other = _active_project("FineCo")
        Product.objects.create(
            project=other, title="OK", slug="ok", price=Decimal("1"), status="active",
        )
        result = control_tasks.daily_store_backup_task()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["created"], 1)
        self.assertTrue(StoreBackupSnapshot.objects.filter(project=other).exists())
        self.assertFalse(StoreBackupSnapshot.objects.filter(project=self.active).exists())

    def test_batch_size_limits_stores_processed_per_run(self):
        for i in range(3):
            p = _active_project(f"Batch{i}")
            Product.objects.create(
                project=p, title="X", slug=f"x{i}", price=Decimal("1"), status="active",
            )
        with patch.object(control_tasks, "BACKUP_BATCH_SIZE", 2):
            result = control_tasks.daily_store_backup_task()
        self.assertEqual(result["created"], 2)
        self.assertEqual(StoreBackupSnapshot.objects.count(), 2)


@override_settings(ALLOWED_HOSTS=["*"])
class AutoBackupToggleTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="ToggleCo", status=Project.Status.ACTIVE, feature_flags={"onboarded": True},
        )
        sub = billing_svc.ensure_subscription(self.project)  # signal already made one, on Basic
        sub.plan = Plan.objects.get(code="growth")
        sub.save(update_fields=["plan"])
        self.owner = User.objects.create_user("tgo", "tgo@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_off_by_default(self):
        body = self.client.get("/admin/backup/").content.decode()
        start = body.index('name="auto_backup"')
        end = body.index(">", start)
        self.assertNotIn("checked", body[start:end])

    def test_turning_it_on_persists(self):
        resp = self.client.post("/admin/backup/auto/", {"auto_backup": "on"})
        self.assertRedirects(resp, "/admin/backup/")
        self.project.refresh_from_db()
        self.assertTrue(self.project.feature_flags["auto_backup"])

    def test_turning_it_off_persists(self):
        self.project.feature_flags = {**self.project.feature_flags, "auto_backup": True}
        self.project.save(update_fields=["feature_flags"])
        self.client.post("/admin/backup/auto/", {})  # unchecked box sends nothing
        self.project.refresh_from_db()
        self.assertFalse(self.project.feature_flags["auto_backup"])

    def test_checked_when_already_on(self):
        self.project.feature_flags = {**self.project.feature_flags, "auto_backup": True}
        self.project.save(update_fields=["feature_flags"])
        body = self.client.get("/admin/backup/").content.decode()
        start = body.index('name="auto_backup"')
        end = body.index(">", start)
        self.assertIn("checked", body[start:end])


@override_settings(ALLOWED_HOSTS=["*"])
class BasicPlanBackupGatingTests(TestCase):
    """Full backup (orders/customers/payments in the manual backup, plus
    automatic daily backups) is a Growth/Pro perk -- a Basic-plan owner
    keeps the plain catalog/CMS/theme manual backup every plan already
    got, just not the "full" tier."""

    def setUp(self):
        self.project = Project.objects.create(
            name="BasicPlanCo", status=Project.Status.ACTIVE, feature_flags={"onboarded": True},
        )
        sub = billing_svc.ensure_subscription(self.project)
        sub.plan = Plan.objects.get(code="basic")
        sub.save(update_fields=["plan"])
        self.owner = User.objects.create_user("bpo", "bpo@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_no_automatic_backup_panel_or_checkbox(self):
        body = self.client.get("/admin/backup/").content.decode()
        self.assertNotIn("Automatic backups", body)
        self.assertNotIn('name="auto_backup"', body)

    def test_upgrade_nudge_shown(self):
        body = self.client.get("/admin/backup/").content.decode()
        self.assertIn("Get automatic daily backups", body)
        self.assertIn("/admin/plan/", body)

    def test_turning_the_checkbox_on_directly_is_rejected(self):
        resp = self.client.post("/admin/backup/auto/", {"auto_backup": "on"}, follow=True)
        self.assertContains(resp, "Growth/Pro feature")
        self.project.refresh_from_db()
        self.assertFalse((self.project.feature_flags or {}).get("auto_backup"))

    def test_manual_download_still_works_catalog_only(self):
        resp = self.client.get("/admin/backup/download/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/zip")

    def test_manual_backup_copy_says_catalog_only(self):
        body = self.client.get("/admin/backup/").content.decode()
        self.assertIn("Orders, customers, coupons and settings are not included.", body)

    def test_daily_task_skips_even_if_flag_somehow_got_set(self):
        # Defence in depth: even if auto_backup were force-set some other
        # way (a downgrade after opting in on Growth, a direct DB edit),
        # the task itself re-checks the plan.
        self.project.feature_flags = {**self.project.feature_flags, "auto_backup": True}
        self.project.save(update_fields=["feature_flags"])
        result = control_tasks.daily_store_backup_task()
        self.assertEqual(result["created"], 0)
        self.assertFalse(StoreBackupSnapshot.objects.filter(project=self.project).exists())


@override_settings(ALLOWED_HOSTS=["*"])
class BackupPageAutoSnapshotsTests(TestCase):
    def setUp(self):
        self.project = _active_project("SnapScreenCo", feature_flags={"onboarded": True})
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
        other = _active_project("OtherSnapCo", feature_flags={"onboarded": True})
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
