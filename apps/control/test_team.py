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


class ResetPasswordTests(TestCase):
    """Generate a new one-time password for an existing team login."""

    def setUp(self):
        self.project = Project.objects.create(name="ResetCo", status="active")
        self.owner = User.objects.create_user(
            username="owner@reset.test", email="owner@reset.test", password="pw"
        )
        Membership.objects.create(
            project=self.project, user=self.owner, role=StoreRole.OWNER, is_active=True
        )
        self.owner2 = User.objects.create_user(
            username="owner2@reset.test", email="owner2@reset.test", password="pw"
        )
        Membership.objects.create(
            project=self.project, user=self.owner2, role=StoreRole.OWNER, is_active=True
        )
        self.manager = User.objects.create_user(
            username="mgr@reset.test", email="mgr@reset.test", password="pw"
        )
        self.mgr_membership = Membership.objects.create(
            project=self.project, user=self.manager, role=StoreRole.MANAGER, is_active=True
        )
        self.staff = User.objects.create_user(
            username="staff@reset.test", email="staff@reset.test", password="pw"
        )
        self.staff_membership = Membership.objects.create(
            project=self.project, user=self.staff, role=StoreRole.STAFF, is_active=True
        )

    def _dgc(self):
        from apps.accounts.models import PlatformRole, Profile
        from apps.billing import services as billing_svc

        billing_svc.ensure_subscription(self.project)
        dgc = User.objects.create_user(
            username="dgc@reset.test", email="dgc@reset.test", password="pw", is_staff=True
        )
        Profile.objects.filter(user=dgc).update(platform_role=PlatformRole.MANAGER)
        self.project.subscription.manager = dgc
        self.project.subscription.save(update_fields=["manager"])
        return User.objects.get(pk=dgc.pk)

    def test_owner_can_reset_anyone(self):
        for target in (self.mgr_membership, self.staff_membership):
            old_hash = target.user.password
            new_password = team_svc.reset_password(
                actor=self.owner, project=self.project, membership=target
            )
            target.user.refresh_from_db()
            self.assertTrue(target.user.check_password(new_password))
            self.assertNotEqual(target.user.password, old_hash)

    def test_owner_cannot_reset_self(self):
        owner_membership = Membership.objects.get(project=self.project, user=self.owner)
        with self.assertRaises(team_svc.TeamError):
            team_svc.reset_password(
                actor=self.owner, project=self.project, membership=owner_membership
            )

    def test_dgc_can_reset_owner_manager_and_staff(self):
        dgc = self._dgc()
        owner_membership = Membership.objects.get(project=self.project, user=self.owner)
        for target in (owner_membership, self.mgr_membership, self.staff_membership):
            new_password = team_svc.reset_password(
                actor=dgc, project=self.project, membership=target
            )
            target.user.refresh_from_db()
            self.assertTrue(target.user.check_password(new_password))

    def test_manager_can_reset_staff_only(self):
        new_password = team_svc.reset_password(
            actor=self.manager, project=self.project, membership=self.staff_membership
        )
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password(new_password))

        owner_membership = Membership.objects.get(project=self.project, user=self.owner)
        with self.assertRaises(PermissionDenied):
            team_svc.reset_password(
                actor=self.manager, project=self.project, membership=owner_membership
            )

    def test_manager_cannot_reset_another_manager(self):
        other_mgr = User.objects.create_user(
            username="mgr2@reset.test", email="mgr2@reset.test", password="pw"
        )
        other_mgr_membership = Membership.objects.create(
            project=self.project, user=other_mgr, role=StoreRole.MANAGER, is_active=True
        )
        with self.assertRaises(PermissionDenied):
            team_svc.reset_password(
                actor=self.manager, project=self.project, membership=other_mgr_membership
            )

    def test_stranger_cannot_reset(self):
        stranger = User.objects.create_user(
            username="stranger@reset.test", email="stranger@reset.test", password="pw"
        )
        with self.assertRaises(PermissionDenied):
            team_svc.reset_password(
                actor=stranger, project=self.project, membership=self.staff_membership
            )

    def test_can_reset_password_capability(self):
        self.assertTrue(team_svc.can_reset_password(self.owner, self.project))
        self.assertFalse(team_svc.can_reset_password(self.manager, self.project))
        self.assertTrue(team_svc.can_reset_password(self._dgc(), self.project))


class ResetPasswordScreenTests(TestCase):
    def setUp(self):
        from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY

        self.project = Project.objects.create(
            name="ResetScreenCo", status="active", feature_flags={"onboarded": True}
        )
        self.owner = User.objects.create_user(
            username="o3@t.test", email="o3@t.test", password="pw", is_staff=True
        )
        Membership.objects.create(
            project=self.project, user=self.owner, role=StoreRole.OWNER, is_active=True
        )
        self.staff = User.objects.create_user(
            username="s3@t.test", email="s3@t.test", password="oldpw", is_staff=True
        )
        self.staff_membership = Membership.objects.create(
            project=self.project, user=self.staff, role=StoreRole.STAFF, is_active=True
        )
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_reset_button_shown_and_flashes_new_password(self):
        resp = self.client.get("/admin/team/")
        self.assertContains(resp, "Reset password")

        resp = self.client.post(
            f"/admin/team/{self.staff_membership.pk}/reset-password/", follow=True
        )
        self.assertContains(resp, "New one-time password for s3@t.test:")
        self.staff.refresh_from_db()
        self.assertFalse(self.staff.check_password("oldpw"))

    def test_manager_screen_hides_reset_for_owner_row(self):
        manager = User.objects.create_user(
            username="m3@t.test", email="m3@t.test", password="pw", is_staff=True
        )
        Membership.objects.create(
            project=self.project, user=manager, role=StoreRole.MANAGER, is_active=True
        )
        self.client.force_login(manager)
        s = self.client.session
        from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY

        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        resp = self.client.get("/admin/team/")
        self.assertContains(resp, "Reset password")   # for the staff row
        owner_membership = Membership.objects.get(project=self.project, user=self.owner)
        resp = self.client.post(
            f"/admin/team/{owner_membership.pk}/reset-password/", follow=True
        )
        self.assertContains(resp, "Only the store owner")
