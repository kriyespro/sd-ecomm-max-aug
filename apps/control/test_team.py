"""Owner / manager provisioning team accounts from the Team screen."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from apps.accounts import team as team_svc
from apps.accounts.models import Membership, StoreRole
from apps.projects.models import Project

User = get_user_model()


class ProvisionMemberTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="TeamCo", status="active")
        self.owner = User.objects.create_user(
            username="owner@t.test", email="owner@t.test", password="pw"
        )
        Membership.objects.create(
            project=self.project, user=self.owner,
            role=StoreRole.OWNER, is_active=True,
        )

    def _manager(self, email="mgr@t.test"):
        u = User.objects.create_user(username=email, email=email, password="pw")
        Membership.objects.create(
            project=self.project, user=u, role=StoreRole.MANAGER, is_active=True
        )
        return u

    def test_new_person_gets_account_and_one_time_password(self):
        m, temp = team_svc.provision_member(
            actor=self.owner, project=self.project,
            email="New@t.test", name="New Person", role="staff",
        )
        self.assertIsNotNone(temp)
        self.assertEqual(len(temp), 12)
        u = m.user
        self.assertEqual(u.email, "new@t.test")
        self.assertEqual(u.first_name, "New")
        self.assertEqual(u.last_name, "Person")
        self.assertTrue(u.is_staff)          # can now reach Mission Control
        self.assertTrue(u.check_password(temp))
        self.assertEqual(m.role, "staff")

    def test_existing_account_is_attached_without_touching_password(self):
        User.objects.create_user(
            username="ex@t.test", email="ex@t.test", password="original"
        )
        m, temp = team_svc.provision_member(
            actor=self.owner, project=self.project, email="ex@t.test", role="manager",
        )
        self.assertIsNone(temp)
        self.assertTrue(m.user.check_password("original"))
        self.assertEqual(m.role, "manager")

    def test_manager_may_provision_staff_only(self):
        mgr = self._manager()
        m, _ = team_svc.provision_member(
            actor=mgr, project=self.project, email="s@t.test", role="staff"
        )
        self.assertEqual(m.role, "staff")

        with self.assertRaises(PermissionDenied):
            team_svc.provision_member(
                actor=mgr, project=self.project, email="m2@t.test", role="manager"
            )

    def test_duplicate_member_rejected(self):
        team_svc.provision_member(
            actor=self.owner, project=self.project, email="dup@t.test", role="staff"
        )
        with self.assertRaises(team_svc.TeamError):
            team_svc.provision_member(
                actor=self.owner, project=self.project, email="dup@t.test", role="staff"
            )

    def test_plan_seat_cap_blocks_the_add(self):
        sub = self.project.subscription
        sub.plan.max_staff = 1          # only the owner fits
        sub.plan.save(update_fields=["max_staff"])
        with self.assertRaises(PermissionDenied):
            team_svc.provision_member(
                actor=self.owner, project=self.project, email="over@t.test", role="staff"
            )

    def test_non_member_cannot_provision(self):
        stranger = User.objects.create_user(
            username="x@t.test", email="x@t.test", password="pw"
        )
        with self.assertRaises(PermissionDenied):
            team_svc.provision_member(
                actor=stranger, project=self.project, email="y@t.test", role="staff"
            )


class TeamScreenTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="ScreenCo", status="active", feature_flags={"onboarded": True}
        )
        self.owner = User.objects.create_user(
            username="o2@t.test", email="o2@t.test", password="pw", is_staff=True
        )
        Membership.objects.create(
            project=self.project, user=self.owner, role=StoreRole.OWNER, is_active=True
        )
        self.client.force_login(self.owner)
        from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY

        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_page_shows_role_reference_and_seats(self):
        resp = self.client.get("/admin/team/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Set up payment providers")   # manager cap bullet
        self.assertContains(resp, "Team seats:")

    def test_add_creates_account_and_flashes_password(self):
        resp = self.client.post(
            "/admin/team/add/",
            {"name": "Sam Staff", "email": "sam@t.test", "role": "staff"},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "One-time password:")
        u = User.objects.get(email="sam@t.test")
        self.assertTrue(
            Membership.objects.filter(
                project=self.project, user=u, role="staff", is_active=True
            ).exists()
        )
