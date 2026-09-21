from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, Profile
from apps.billing import services as billing_svc
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ActivityFeedTenantScopingTests(TestCase):
    """A DGC/store user must only ever see their own store's audit log and
    counts on the Mission Control dashboard — never another tenant's."""

    def setUp(self):
        # dashboard_stats() caches by user pk (control/services.py); a fresh
        # DB per test doesn't reset this process-level cache, and SQLite's
        # rolled-back transactions mean a test user's pk gets reused across
        # methods -- clear it so an earlier test's cached counts can't leak.
        cache.clear()
        self.own_project = Project.objects.create(
            name="Own Co", status="active", feature_flags={"onboarded": True}
        )
        self.other_project = Project.objects.create(
            name="Other Co", status="active", feature_flags={"onboarded": True}
        )
        billing_svc.ensure_subscription(self.own_project)
        billing_svc.ensure_subscription(self.other_project)

        self.owner = User.objects.create_user(
            "own", "own@t.test", "pw", is_staff=True
        )
        Membership.objects.create(user=self.owner, project=self.own_project, role="owner")

        self.dgc = User.objects.create_user("dgc", "dgc@t.test", "pw", is_staff=True)
        Profile.objects.update_or_create(
            user=self.dgc, defaults={"platform_role": PlatformRole.MANAGER}
        )
        # create_user()'s post_save signal (ensure_profile) constructs a
        # Profile(user=self.dgc) to get_or_create it, which caches that
        # (default-role) instance as self.dgc's reverse .profile relation --
        # the update_or_create above changes the DB row but not that cache.
        # Refresh so self.dgc.profile (used by is_platform_staff() below)
        # reads the updated role instead of a stale pre-update snapshot.
        self.dgc.refresh_from_db()
        sub = self.own_project.subscription
        sub.manager = self.dgc
        sub.save(update_fields=["manager"])

        self.superuser = User.objects.create_superuser("root", "root@t.test", "pw")

        self.own_log = record_audit(
            actor=self.owner, project=self.own_project, action=AuditLog.Action.CREATE,
            target=self.own_project, changes={"signup": "self-serve", "owner": "own@t.test"},
        )
        self.other_log = record_audit(
            actor=None, project=self.other_project, action=AuditLog.Action.CREATE,
            target=self.other_project, changes={"signup": "self-serve", "owner": "other@t.test"},
        )

    def _login_dgc(self):
        self.client.force_login(self.dgc)
        session = self.client.session
        session["active_project_id"] = self.own_project.pk
        session.save()

    def test_dgc_activity_feed_excludes_other_tenants(self):
        self._login_dgc()
        resp = self.client.get("/admin/")
        self.assertContains(resp, "Own Co")
        self.assertNotContains(resp, "Other Co")
        self.assertNotContains(resp, "other@t.test")

    def test_dgc_activity_feed_partial_excludes_other_tenants(self):
        self._login_dgc()
        resp = self.client.get("/admin/activity/")
        self.assertContains(resp, "Own Co")
        self.assertNotContains(resp, "Other Co")
        self.assertNotContains(resp, "other@t.test")

    def test_dgc_stats_scoped_to_own_projects(self):
        from apps.control import services

        stats = services.dashboard_stats(self.dgc)
        self.assertEqual(stats["total_projects"], 1)
        admin_stats = services.dashboard_stats(self.superuser)
        self.assertEqual(admin_stats["total_projects"], 2)

    def test_dgc_dashboard_shows_simplified_scoped_cards(self):
        # DGC gets its own store-scoped labels, not the platform-growth
        # labels admin sees -- and no dead "Revenue today" placeholder.
        self.client.force_login(self.dgc)
        resp = self.client.get("/admin/")
        self.assertContains(resp, "Stores you manage")
        self.assertContains(resp, "Active stores")
        self.assertContains(resp, "Team members")
        self.assertNotContains(resp, "Revenue today")
        self.assertNotContains(resp, "Total users")
        self.assertNotContains(resp, "Affiliate signups")

    def test_admin_dashboard_keeps_growth_cards_no_dead_revenue_stat(self):
        self.client.force_login(self.superuser)
        resp = self.client.get("/admin/")
        self.assertContains(resp, "Total users")
        self.assertContains(resp, "Affiliate signups")
        self.assertNotContains(resp, "Revenue today")

    def test_dashboard_stats_no_longer_has_dead_revenue_key(self):
        from apps.control import services

        stats = services.dashboard_stats(self.dgc)
        self.assertNotIn("revenue_today", stats)

    def test_superuser_sees_every_tenant(self):
        self.client.force_login(self.superuser)
        resp = self.client.get("/admin/")
        self.assertContains(resp, "Own Co")
        self.assertContains(resp, "Other Co")

    def test_store_owner_cannot_see_other_tenant_via_activity_partial(self):
        self.client.force_login(self.owner)
        session = self.client.session
        session["active_project_id"] = self.own_project.pk
        session.save()
        resp = self.client.get("/admin/activity/")
        self.assertNotContains(resp, "Other Co")
        self.assertNotContains(resp, "other@t.test")
