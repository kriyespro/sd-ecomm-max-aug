from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from apps.accounts.affiliate_signup import join_as_affiliate
from apps.accounts.models import Membership, PlatformRole, Profile
from apps.projects.models import Project

User = get_user_model()

GOOGLE_ON = dict(
    GOOGLE_OAUTH_CLIENT_ID="cid.apps.googleusercontent.com",
    GOOGLE_OAUTH_CLIENT_SECRET="secret",
    GOOGLE_OAUTH_REDIRECT_URI="https://shopinaday.com/accounts/google/callback/",
)


@override_settings(ALLOWED_HOSTS=["*"])
class JoinAsAffiliateServiceTests(TestCase):
    def test_new_email_creates_a_manager_account(self):
        user, created, upgraded = join_as_affiliate(name="Ann Aff", email="ann@aff.test")
        self.assertTrue(created)
        self.assertTrue(upgraded)
        self.assertTrue(user.is_staff)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.profile.platform_role, PlatformRole.MANAGER)

    def test_existing_store_owner_gets_upgraded_not_replaced(self):
        project = Project.objects.create(name="Owner Co", status="active")
        owner = User.objects.create_user("own", "own@aff.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=project, role="owner")

        user, created, upgraded = join_as_affiliate(name="Own Er", email="own@aff.test")
        self.assertFalse(created)
        self.assertTrue(upgraded)
        self.assertEqual(user.pk, owner.pk)
        self.assertEqual(user.profile.platform_role, PlatformRole.MANAGER)
        self.assertTrue(Membership.objects.filter(user=owner, project=project).exists())

    def test_already_a_manager_is_a_no_op_upgrade(self):
        dgc = User.objects.create_user("dgc", "dgc@aff.test", "pw", is_staff=True)
        Profile.objects.filter(user=dgc).update(platform_role=PlatformRole.MANAGER)

        user, created, upgraded = join_as_affiliate(name="D", email="dgc@aff.test")
        self.assertFalse(created)
        self.assertFalse(upgraded)
        self.assertEqual(user.pk, dgc.pk)

    def test_platform_owner_is_never_downgraded(self):
        owner = User.objects.create_user("po", "po@aff.test", "pw", is_staff=True)
        Profile.objects.filter(user=owner).update(platform_role=PlatformRole.OWNER)

        user, created, upgraded = join_as_affiliate(name="P", email="po@aff.test")
        self.assertFalse(upgraded)
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.platform_role, PlatformRole.OWNER)

    def test_banned_account_is_rejected(self):
        banned = User.objects.create_user("b", "banned@aff.test", "pw", is_staff=True)
        Profile.objects.filter(user=banned).update(is_banned=True)

        with self.assertRaises(ValidationError):
            join_as_affiliate(name="B", email="banned@aff.test")

    def test_blank_email_rejected(self):
        with self.assertRaises(ValidationError):
            join_as_affiliate(name="X", email="")


@override_settings(ALLOWED_HOSTS=["*"])
class AffiliateJoinPageTests(TestCase):
    @override_settings(**GOOGLE_ON)
    def test_shows_google_button(self):
        resp = self.client.get("/accounts/affiliate/")
        self.assertContains(resp, "Continue with Google")
        self.assertContains(resp, "/accounts/google/start/?kind=affiliate")

    def test_notice_when_google_not_configured(self):
        resp = self.client.get("/accounts/affiliate/")
        self.assertContains(resp, "isn't configured")

    def test_logged_in_owner_joins_instantly_no_oauth_needed(self):
        project = Project.objects.create(name="Instant Co", status="active")
        owner = User.objects.create_user("io", "io@aff.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=project, role="owner")
        self.client.force_login(owner)

        resp = self.client.get("/accounts/affiliate/", follow=True)
        self.assertRedirects(resp, "/admin/earnings/")
        owner.profile.refresh_from_db()
        self.assertEqual(owner.profile.platform_role, PlatformRole.MANAGER)

    def test_logged_in_already_affiliate_just_redirects(self):
        dgc = User.objects.create_user("d2", "d2@aff.test", "pw", is_staff=True)
        Profile.objects.filter(user=dgc).update(platform_role=PlatformRole.MANAGER)
        self.client.force_login(dgc)

        resp = self.client.get("/accounts/affiliate/", follow=True)
        self.assertRedirects(resp, "/admin/earnings/")


@override_settings(ALLOWED_HOSTS=["*"], **GOOGLE_ON)
class AffiliateGoogleCallbackTests(TestCase):
    def _start(self):
        self.client.get("/accounts/google/start/?kind=affiliate")
        return self.client.session["google_oauth_flow"]["state"]

    def test_new_google_account_becomes_affiliate_and_lands_on_earnings(self):
        state = self._start()
        with patch("apps.accounts.views.google_oauth.exchange_code") as ex:
            ex.return_value = {"email": "newaff@gmail.test", "email_verified": True,
                               "name": "New Aff", "sub": "901"}
            resp = self.client.get(
                f"/accounts/google/callback/?code=abc&state={state}", follow=True,
            )
        self.assertRedirects(resp, "/admin/earnings/")
        user = User.objects.get(email="newaff@gmail.test")
        self.assertEqual(user.profile.platform_role, PlatformRole.MANAGER)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_bad_state_returns_to_affiliate_join_not_store_signup(self):
        self._start()
        resp = self.client.get("/accounts/google/callback/?code=abc&state=wrong")
        self.assertRedirects(resp, "/accounts/affiliate/", fetch_redirect_response=False)

    def test_store_signup_flow_unaffected_default_kind(self):
        self.client.get("/accounts/google/start/")
        state = self.client.session["google_oauth_flow"]["state"]
        with patch("apps.accounts.views.google_oauth.exchange_code") as ex:
            ex.return_value = {"email": "storeperson@gmail.test", "email_verified": True,
                               "name": "Store Person", "sub": "902"}
            resp = self.client.get(f"/accounts/google/callback/?code=abc&state={state}")
        self.assertRedirects(resp, "/accounts/signup/complete/", fetch_redirect_response=False)
