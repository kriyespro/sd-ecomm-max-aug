"""Confirming a team invite — the only place provision_member/add_member's
PendingVerification actually takes effect (see apps.control.test_team for
the invite-creation side)."""

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts import team as team_svc
from apps.accounts.models import Membership, PendingVerification, StoreRole
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"], EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ConfirmInviteTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="InviteCo", status="active")
        self.owner = User.objects.create_user(
            username="owner@inv.test", email="owner@inv.test", password="pw"
        )
        Membership.objects.create(
            project=self.project, user=self.owner, role=StoreRole.OWNER, is_active=True
        )

    def _invite(self, email="new@inv.test", role="staff", name="New Person"):
        with self.captureOnCommitCallbacks(execute=True):
            return team_svc.provision_member(
                actor=self.owner, project=self.project, email=email, name=name, role=role,
            )

    def test_email_is_sent_with_a_working_link(self):
        invite = self._invite()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(invite.token, mail.outbox[0].body)
        self.assertIn("InviteCo", mail.outbox[0].body)

    def test_new_account_accept_sets_own_password_and_logs_in(self):
        invite = self._invite()
        resp = self.client.post(
            f"/accounts/invite/{invite.token}/", {"password": "a-strong-pw-123"}
        )
        self.assertRedirects(resp, "/admin/", fetch_redirect_response=False)
        u = User.objects.get(email="new@inv.test")
        self.assertTrue(u.check_password("a-strong-pw-123"))
        self.assertTrue(u.first_name == "New")
        self.assertTrue(
            Membership.objects.filter(project=self.project, user=u, role="staff", is_active=True).exists()
        )
        invite.refresh_from_db()
        self.assertTrue(invite.is_used)
        self.assertEqual(int(self.client.session["_auth_user_id"]), u.pk)

    def test_new_account_accept_requires_a_password(self):
        invite = self._invite()
        resp = self.client.post(f"/accounts/invite/{invite.token}/", {})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="new@inv.test").exists())

    def test_existing_account_accept_does_not_prompt_for_a_password(self):
        User.objects.create_user(
            username="ex@inv.test", email="ex@inv.test", password="original"
        )
        invite = self._invite(email="ex@inv.test", role="manager")
        resp = self.client.get(f"/accounts/invite/{invite.token}/")
        self.assertNotContains(resp, 'name="password"')

        resp = self.client.post(f"/accounts/invite/{invite.token}/", {})
        self.assertRedirects(resp, "/admin/", fetch_redirect_response=False)
        u = User.objects.get(email="ex@inv.test")
        self.assertTrue(u.check_password("original"))
        self.assertTrue(
            Membership.objects.filter(project=self.project, user=u, role="manager", is_active=True).exists()
        )

    def test_expired_invite_is_rejected(self):
        invite = self._invite()
        invite.expires_at = timezone.now() - timezone.timedelta(days=1)
        invite.save(update_fields=["expires_at"])
        resp = self.client.post(f"/accounts/invite/{invite.token}/", {"password": "x"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="new@inv.test").exists())

    def test_used_invite_cannot_be_reused(self):
        invite = self._invite()
        self.client.post(f"/accounts/invite/{invite.token}/", {"password": "a-strong-pw-123"})
        self.client.get("/accounts/logout/")
        resp = self.client.post(f"/accounts/invite/{invite.token}/", {"password": "another-pw-456"})
        self.assertEqual(resp.status_code, 200)
        u = User.objects.get(email="new@inv.test")
        self.assertTrue(u.check_password("a-strong-pw-123"))

    def test_unknown_token_shows_invalid_not_a_500(self):
        resp = self.client.get("/accounts/invite/does-not-exist/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "no longer valid")

    def test_seat_cap_hit_between_invite_and_accept_is_rejected(self):
        invite = self._invite(email="late@inv.test")
        sub = self.project.subscription
        sub.plan.max_staff = 1  # only the owner fits, by the time they click
        sub.plan.save(update_fields=["max_staff"])
        resp = self.client.post(f"/accounts/invite/{invite.token}/", {"password": "x"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="late@inv.test").exists())

    def test_resend_rotates_token_and_old_link_stops_working(self):
        invite = self._invite()
        old_token = invite.token
        with self.captureOnCommitCallbacks(execute=True):
            team_svc.resend_invite(actor=self.owner, project=self.project, invite=invite)
        invite.refresh_from_db()
        self.assertNotEqual(invite.token, old_token)
        self.assertEqual(len(mail.outbox), 2)
        resp = self.client.get(f"/accounts/invite/{old_token}/")
        self.assertContains(resp, "no longer valid")

    def test_revoke_deletes_the_invite(self):
        invite = self._invite()
        team_svc.revoke_invite(actor=self.owner, project=self.project, invite=invite)
        self.assertFalse(PendingVerification.objects.filter(pk=invite.pk).exists())
