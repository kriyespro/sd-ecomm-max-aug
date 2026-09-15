"""Mission Control screens: store-side support/ideas + the admin triage queue."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.billing import services as billing_svc
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project
from apps.support.models import ReporterRole, Ticket, TicketKind, TicketStatus

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class SupportScreensTests(TestCase):
    def setUp(self):
        self.store_a = Project.objects.create(name="ShopA", status="active",
                                              feature_flags={"onboarded": True})
        self.store_b = Project.objects.create(name="ShopB", status="active",
                                              feature_flags={"onboarded": True})
        sub_a = billing_svc.ensure_subscription(self.store_a)

        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store_a, user=self.owner, role=StoreRole.OWNER)
        self.staff = User.objects.create_user("s", "s@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store_a, user=self.staff, role=StoreRole.STAFF)

        self.other_owner = User.objects.create_user("o2", "o2@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store_b, user=self.other_owner, role=StoreRole.OWNER)

        self.dgc = User.objects.create_user("d", "d@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=self.dgc).update(platform_role=PlatformRole.MANAGER)
        sub_a.manager = self.dgc
        sub_a.save(update_fields=["manager"])
        self.dgc = User.objects.get(pk=self.dgc.pk)

        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")

    def _login(self, user, store):
        self.client.force_login(user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = store.pk
        s.save()

    # --------------------------------------------------------------- access

    def test_owner_sees_support_screen(self):
        self._login(self.owner, self.store_a)
        resp = self.client.get("/admin/support/")
        self.assertEqual(resp.status_code, 200)

    def test_staff_is_denied(self):
        self._login(self.staff, self.store_a)
        resp = self.client.get("/admin/support/")
        self.assertEqual(resp.status_code, 403)

    def test_dgc_sees_support_screen(self):
        self._login(self.dgc, self.store_a)
        resp = self.client.get("/admin/support/")
        self.assertEqual(resp.status_code, 200)

    def test_non_admin_cannot_reach_queue(self):
        self._login(self.owner, self.store_a)
        resp = self.client.get("/admin/tickets/")
        self.assertEqual(resp.status_code, 403)

    def test_admin_reaches_queue(self):
        self._login(self.admin, self.store_a)
        resp = self.client.get("/admin/tickets/")
        self.assertEqual(resp.status_code, 200)

    # -------------------------------------------------------------- privacy

    def test_private_ticket_hidden_from_other_store(self):
        ticket = Ticket.objects.create(
            project=self.store_a, created_by=self.owner, created_by_role=ReporterRole.OWNER,
            kind=TicketKind.BUG, subject="Broken checkout", description="x",
        )
        self._login(self.other_owner, self.store_b)
        resp = self.client.get(f"/admin/support/{ticket.pk}/")
        self.assertEqual(resp.status_code, 404)

    def test_feature_request_visible_across_stores(self):
        ticket = Ticket.objects.create(
            project=self.store_a, created_by=self.owner, created_by_role=ReporterRole.OWNER,
            kind=TicketKind.FEATURE_REQUEST, subject="Bulk export", description="x",
        )
        self._login(self.other_owner, self.store_b)
        resp = self.client.get(f"/admin/support/{ticket.pk}/")
        self.assertEqual(resp.status_code, 200)

    # ------------------------------------------------------------ workflow

    def test_create_reply_and_reopen_flow(self):
        self._login(self.owner, self.store_a)
        resp = self.client.post("/admin/support/new/", {
            "kind": TicketKind.SUPPORT, "subject": "Payments down",
            "description": "Razorpay checkout 500s", "priority": "high",
        })
        self.assertEqual(resp.status_code, 302)
        ticket = Ticket.objects.get(subject="Payments down")
        self.assertEqual(ticket.project_id, self.store_a.pk)
        self.assertEqual(ticket.created_by_role, ReporterRole.OWNER)

        self._login(self.admin, self.store_a)
        resp = self.client.post(f"/admin/tickets/{ticket.pk}/update/", {
            "status": TicketStatus.RESOLVED, "priority": "high", "assigned_to": self.admin.pk,
        })
        self.assertEqual(resp.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.RESOLVED)
        self.assertIsNotNone(ticket.resolved_at)

        self._login(self.owner, self.store_a)
        resp = self.client.post(f"/admin/support/{ticket.pk}/reply/", {"body": "Still failing"})
        self.assertEqual(resp.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.OPEN)
        self.assertEqual(ticket.messages.count(), 1)

    def test_vote_toggle(self):
        ticket = Ticket.objects.create(
            project=self.store_a, created_by=self.owner, created_by_role=ReporterRole.OWNER,
            kind=TicketKind.FEATURE_REQUEST, subject="Bulk export", description="x",
        )
        self._login(self.other_owner, self.store_b)
        self.client.post(f"/admin/support/{ticket.pk}/vote/")
        self.assertEqual(ticket.votes.count(), 1)
        self.client.post(f"/admin/support/{ticket.pk}/vote/")
        self.assertEqual(ticket.votes.count(), 0)

    def test_internal_note_not_in_store_thread(self):
        ticket = Ticket.objects.create(
            project=self.store_a, created_by=self.owner, created_by_role=ReporterRole.OWNER,
            kind=TicketKind.BUG, subject="Broken checkout", description="x",
        )
        self._login(self.admin, self.store_a)
        self.client.post(f"/admin/tickets/{ticket.pk}/reply/", {
            "body": "internal-only note", "is_internal_note": "on",
        })
        self._login(self.owner, self.store_a)
        resp = self.client.get(f"/admin/support/{ticket.pk}/")
        self.assertNotContains(resp, "internal-only note")
