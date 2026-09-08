from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, Profile
from apps.billing import services as billing_svc
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class DgcManagedStoreHidesPlanTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Managed Co", status="active", feature_flags={"onboarded": True}
        )
        self.sub = billing_svc.ensure_subscription(self.project)

        self.owner = User.objects.create_user(
            "own", "own@managed.test", "pw", is_staff=True
        )
        Membership.objects.create(user=self.owner, project=self.project, role="owner")

        self.dgc = User.objects.create_user("dgc", "dgc@t.test", "pw", is_staff=True)
        Profile.objects.update_or_create(
            user=self.dgc, defaults={"platform_role": PlatformRole.MANAGER}
        )

    def _login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def _make_dgc_managed(self):
        self.sub.manager = self.dgc
        self.sub.save(update_fields=["manager"])

    # --- not managed: owner has normal billing access ---
    def test_owner_sees_plan_when_not_dgc_managed(self):
        self._login(self.owner)
        resp = self.client.get("/admin/products/")
        self.assertContains(resp, 'href="/admin/plan/"')
        self.assertEqual(self.client.get("/admin/plan/").status_code, 200)

    # --- managed: hidden + blocked for the store team ---
    def test_owner_loses_plan_when_dgc_managed(self):
        self._make_dgc_managed()
        self._login(self.owner)
        resp = self.client.get("/admin/products/")
        self.assertNotContains(resp, 'href="/admin/plan/"')
        self.assertEqual(self.client.get("/admin/plan/").status_code, 403)

    # --- platform staff still get in ---
    def test_platform_staff_still_see_plan_for_managed_store(self):
        self._make_dgc_managed()
        su = User.objects.create_superuser("root", "root@t.test", "pw")
        self._login(su)
        self.assertEqual(self.client.get("/admin/plan/").status_code, 200)

    # --- a DGC's own earnings screen ---
    def test_dgc_earnings_screen_shows_only_own_rows(self):
        from decimal import Decimal

        from django.utils import timezone

        from apps.billing.models import Invoice, ManagerCommission

        self._make_dgc_managed()
        other = User.objects.create_user("dgc2", "dgc2@t.test", "pw", is_staff=True)
        Profile.objects.update_or_create(
            user=other, defaults={"platform_role": PlatformRole.MANAGER}
        )
        now = timezone.now()
        inv_kw = dict(
            subscription=self.sub, amount=Decimal("1000"), status="paid",
            period_start=now, period_end=now, due_at=now,
        )
        inv_a = Invoice.objects.create(number="INV-A", **inv_kw)
        inv_b = Invoice.objects.create(number="INV-B", **inv_kw)
        ManagerCommission.objects.create(
            manager=self.dgc, subscription=self.sub, invoice=inv_a, period="monthly",
            base_amount=Decimal("1000"), rate_pct=Decimal("30"), amount=Decimal("300"),
        )
        ManagerCommission.objects.create(
            manager=other, subscription=self.sub, invoice=inv_b, period="monthly",
            base_amount=Decimal("1000"), rate_pct=Decimal("30"), amount=Decimal("999"),
        )

        self._login(self.dgc)
        resp = self.client.get("/admin/earnings/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "300")
        self.assertNotContains(resp, "999")

    def test_store_staff_cannot_reach_earnings_screen(self):
        staff = User.objects.create_user("st", "st@managed.test", "pw", is_staff=True)
        Membership.objects.create(user=staff, project=self.project, role="staff")
        self._login(staff)
        self.assertEqual(self.client.get("/admin/earnings/").status_code, 403)

    def test_dgc_nav_shows_earnings_not_platform_billing(self):
        self._make_dgc_managed()
        self._login(self.dgc)
        resp = self.client.get("/admin/stores/")
        self.assertContains(resp, 'href="/admin/earnings/"')
        self.assertNotContains(resp, 'href="/admin/billing/commissions/"')


class DgcTeamCapsTests(TestCase):
    """A DGC running a store for a client manages the client's staff only —
    not owner/manager roles, and cannot escalate itself into a lasting seat."""

    def setUp(self):
        from apps.accounts import team as team_svc

        self.team_svc = team_svc
        self.project = Project.objects.create(name="Client Co", status="active")
        self.sub = billing_svc.ensure_subscription(self.project)
        self.owner = User.objects.create_user("o", "o@client.test", "pw", is_staff=True)
        Membership.objects.create(user=self.owner, project=self.project, role="owner")
        dgc = User.objects.create_user("d", "d@dgc.test", "pw", is_staff=True)
        Profile.objects.update_or_create(
            user=dgc, defaults={"platform_role": PlatformRole.MANAGER}
        )
        self.sub.manager = dgc
        self.sub.save(update_fields=["manager"])
        self.dgc = User.objects.get(pk=dgc.pk)  # fresh — no stale profile cache

    def test_dgc_can_add_staff(self):
        User.objects.create_user("s", "s@x.test", "pw")
        m = self.team_svc.add_member(
            actor=self.dgc, project=self.project, email="s@x.test", role="staff",
        )
        self.assertEqual(m.role, "staff")

    def test_dgc_cannot_add_manager_or_owner(self):
        from django.core.exceptions import PermissionDenied

        User.objects.create_user("m", "m@x.test", "pw")
        for role in ("manager", "owner"):
            with self.assertRaises(PermissionDenied):
                self.team_svc.add_member(
                    actor=self.dgc, project=self.project, email="m@x.test", role=role,
                )

    def test_platform_admin_keeps_full_team_control(self):
        su = User.objects.create_superuser("root", "root@t.test", "pw")
        User.objects.create_user("m2", "m2@x.test", "pw")
        m = self.team_svc.add_member(
            actor=su, project=self.project, email="m2@x.test", role="manager",
        )
        self.assertEqual(m.role, "manager")
