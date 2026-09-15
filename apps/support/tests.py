from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.projects.models import Project
from apps.support import services as support_svc
from apps.support.models import ReporterRole, Ticket, TicketKind, TicketStatus, reporter_role_for

User = get_user_model()


class ReporterRoleForTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="ShopCo", status="active")

    def test_owner(self):
        u = User.objects.create_user("o", "o@t.test", "pw")
        Membership.objects.create(project=self.project, user=u, role=StoreRole.OWNER)
        self.assertEqual(reporter_role_for(u, self.project), ReporterRole.OWNER)

    def test_manager(self):
        u = User.objects.create_user("m", "m@t.test", "pw")
        Membership.objects.create(project=self.project, user=u, role=StoreRole.MANAGER)
        self.assertEqual(reporter_role_for(u, self.project), ReporterRole.MANAGER)

    def test_dgc(self):
        u = User.objects.create_user("d", "d@t.test", "pw")
        Profile.objects.filter(user=u).update(platform_role=PlatformRole.MANAGER)
        u = User.objects.get(pk=u.pk)
        self.assertEqual(reporter_role_for(u, self.project), ReporterRole.DGC)

    def test_admin(self):
        u = User.objects.create_superuser("root", "root@t.test", "pw")
        self.assertEqual(reporter_role_for(u, self.project), ReporterRole.ADMIN)


class AddMessageTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="ShopCo", status="active")
        self.owner = User.objects.create_user("o", "o@t.test", "pw")
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")
        self.ticket = Ticket.objects.create(
            project=self.project, created_by=self.owner, created_by_role=ReporterRole.OWNER,
            kind=TicketKind.SUPPORT, subject="Checkout broken", description="Help",
        )

    def test_reporter_reply_reopens_closed_ticket(self):
        support_svc.set_status(self.ticket, TicketStatus.CLOSED)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, TicketStatus.CLOSED)
        self.assertIsNotNone(self.ticket.resolved_at)

        support_svc.add_message(self.ticket, author=self.owner, author_role=ReporterRole.OWNER,
                                body="Still broken")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, TicketStatus.OPEN)
        self.assertIsNone(self.ticket.resolved_at)

    def test_admin_reply_does_not_reopen(self):
        support_svc.set_status(self.ticket, TicketStatus.RESOLVED)
        support_svc.add_message(self.ticket, author=self.admin, author_role=ReporterRole.ADMIN,
                                body="Closing this out")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, TicketStatus.RESOLVED)

    def test_internal_note_never_reopens(self):
        support_svc.set_status(self.ticket, TicketStatus.CLOSED)
        support_svc.add_message(self.ticket, author=self.admin, author_role=ReporterRole.ADMIN,
                                body="internal only", is_internal_note=True)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, TicketStatus.CLOSED)


class ToggleVoteTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="ShopCo", status="active")
        self.owner = User.objects.create_user("o", "o@t.test", "pw")
        self.ticket = Ticket.objects.create(
            project=self.project, created_by=self.owner, created_by_role=ReporterRole.OWNER,
            kind=TicketKind.FEATURE_REQUEST, subject="Bulk export", description="Please",
        )

    def test_toggle_adds_then_removes(self):
        self.assertTrue(support_svc.toggle_vote(self.ticket, self.owner))
        self.assertEqual(self.ticket.votes.count(), 1)
        self.assertFalse(support_svc.toggle_vote(self.ticket, self.owner))
        self.assertEqual(self.ticket.votes.count(), 0)
