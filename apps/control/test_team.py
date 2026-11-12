"""Owner / manager inviting team members from the Team screen.

Nothing is attached (and no account is created) until the invited address
confirms by email — see apps.accounts.test_team_invite for the accept-flow
tests. Attaching instantly used to let anyone type in an email they didn't
own and either gain visibility into that account or (for a brand-new
address) hold credentials to it, shown only to the inviter."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from apps.accounts import team as team_svc
from apps.accounts.models import Membership, PendingVerification, StoreRole
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

    def test_new_person_gets_a_pending_invite_no_account_yet(self):
        with self.captureOnCommitCallbacks(execute=True):
            invite = team_svc.provision_member(
                actor=self.owner, project=self.project,
                email="New@t.test", name="New Person", role="staff",
            )
        self.assertEqual(invite.kind, PendingVerification.Kind.TEAM_INVITE)
        self.assertEqual(invite.email, "new@t.test")
        self.assertEqual(invite.role, "staff")
        self.assertEqual(invite.invited_name, "New Person")
        self.assertFalse(invite.is_used)
        self.assertFalse(User.objects.filter(email="new@t.test").exists())
        self.assertFalse(Membership.objects.filter(project=self.project, user__email="new@t.test").exists())

    def test_existing_account_gets_a_pending_invite_not_instant_attach(self):
        User.objects.create_user(
            username="ex@t.test", email="ex@t.test", password="original"
        )
        invite = team_svc.provision_member(
            actor=self.owner, project=self.project, email="ex@t.test", role="manager",
        )
        self.assertFalse(invite.is_used)
        self.assertFalse(
            Membership.objects.filter(project=self.project, user__email="ex@t.test").exists()
        )
        u = User.objects.get(email="ex@t.test")
        self.assertTrue(u.check_password("original"))

    def test_manager_may_provision_staff_only(self):
        mgr = self._manager()
        invite = team_svc.provision_member(
            actor=mgr, project=self.project, email="s@t.test", role="staff"
        )
        self.assertEqual(invite.role, "staff")

        with self.assertRaises(PermissionDenied):
            team_svc.provision_member(
                actor=mgr, project=self.project, email="m2@t.test", role="manager"
            )

    def test_duplicate_invite_replaces_the_old_one(self):
        first = team_svc.provision_member(
            actor=self.owner, project=self.project, email="dup@t.test", role="staff"
        )
        second = team_svc.provision_member(
            actor=self.owner, project=self.project, email="dup@t.test", role="manager"
        )
        self.assertFalse(PendingVerification.objects.filter(pk=first.pk).exists())
        self.assertEqual(second.role, "manager")

    def test_already_on_team_rejected(self):
        u = User.objects.create_user(username="on@t.test", email="on@t.test", password="pw")
        Membership.objects.create(
            project=self.project, user=u, role=StoreRole.STAFF, is_active=True
        )
        with self.assertRaises(team_svc.TeamError):
            team_svc.provision_member(
                actor=self.owner, project=self.project, email="on@t.test", role="staff"
            )

    def test_plan_seat_cap_blocks_the_invite(self):
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

    def test_add_sends_invite_nothing_attached_yet(self):
        from apps.accounts.models import PendingVerification

        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                "/admin/team/add/",
                {"name": "Sam Staff", "email": "sam@t.test", "role": "staff"},
                follow=True,
            )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Invite sent to sam@t.test")
        self.assertFalse(User.objects.filter(email="sam@t.test").exists())
        self.assertTrue(
            PendingVerification.objects.filter(
                kind=PendingVerification.Kind.TEAM_INVITE, email="sam@t.test",
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
