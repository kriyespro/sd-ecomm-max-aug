import re

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership
from apps.cms.models import StoreProfile
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Domain, Project


class StoreProfileViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="boss", email="boss@t.test", password="pw"
        )
        self.project = Project.objects.create(name="CtlStore", status="active")
        self.client.force_login(self.user)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def test_get_renders_form(self):
        resp = self.client.get("/admin/cms/store-profile/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Store profile")

    def test_post_creates_profile(self):
        resp = self.client.post(
            "/admin/cms/store-profile/",
            {
                "tagline": "Handmade in Pune",
                "support_email": "help@ctlstore.test",
                "support_phone": "+919812345678",
                "whatsapp": "+919812345678",
                "address": "12 MG Road\nPune 411001",
                "gstin": "27ABCDE1234F1Z5",
                "instagram_url": "https://instagram.com/ctlstore",
                "facebook_url": "",
                "youtube_url": "",
                "x_url": "",
                "copyright_text": "",
                "show_payment_icons": "on",
            },
        )
        self.assertEqual(resp.status_code, 302)
        profile = StoreProfile.objects.get(project=self.project)
        self.assertEqual(profile.tagline, "Handmade in Pune")
        self.assertEqual(profile.support_phone, "+919812345678")
        self.assertEqual(profile.whatsapp_link, "https://wa.me/919812345678")

    def test_second_post_updates_same_row(self):
        for tag in ("first", "second"):
            self.client.post(
                "/admin/cms/store-profile/",
                {
                    "tagline": tag, "support_email": "", "support_phone": "",
                    "whatsapp": "", "address": "", "gstin": "",
                    "instagram_url": "", "facebook_url": "", "youtube_url": "",
                    "x_url": "", "copyright_text": "", "show_payment_icons": "on",
                },
            )
        self.assertEqual(StoreProfile.objects.filter(project=self.project).count(), 1)
        self.assertEqual(
            StoreProfile.objects.get(project=self.project).tagline, "second"
        )


@override_settings(ALLOWED_HOSTS=["*"])
class DashboardRoutingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.a = Project.objects.create(name="StoreA", status="active")
        self.b = Project.objects.create(name="StoreB", status="active")
        Domain.objects.create(project=self.a, host="a.test", is_verified=True)
        self.owner = User.objects.create_user(
            username="o2", email="o2@t.test", password="pw", is_staff=True
        )
        Membership.objects.create(user=self.owner, project=self.a, role="owner")
        Membership.objects.create(user=self.owner, project=self.b, role="owner")
        self.client.force_login(self.owner)

    def test_multi_store_owner_on_platform_host_goes_to_picker(self):
        resp = self.client.get("/admin/", HTTP_HOST="mnxstore.test")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/admin/choose-store/")

    def test_owner_on_store_domain_lands_in_that_store(self):
        resp = self.client.get("/admin/", HTTP_HOST="a.test", follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "StoreA")
        self.assertContains(resp, "Products")

    def test_superuser_still_sees_platform_dashboard(self):
        su = get_user_model().objects.create_superuser(
            username="root", email="r@t.test", password="pw"
        )
        self.client.force_login(su)
        resp = self.client.get("/admin/", HTTP_HOST="mnxstore.test")
        self.assertEqual(resp.status_code, 200)


@override_settings(ALLOWED_HOSTS=["*"])
class ChromeThemeByRoleTests(TestCase):
    """Mission Control chrome colour follows the viewer's highest role:
    platform=indigo, DGC=orange, store owner=emerald, store manager=rose."""

    def setUp(self):
        self.project = Project.objects.create(name="TintCo", status="active")
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def _hue(self, path="/admin/products/"):
        body = self.client.get(path, HTTP_HOST="testserver", follow=True).content.decode()
        m = re.search(r"<aside class=\"[^\"]*?bg-([a-z]+)-950", body)
        return m.group(1) if m else None

    def _login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()

    def test_superuser_is_indigo_everywhere(self):
        su = get_user_model().objects.create_superuser("root", "r@t.test", "pw")
        self._login(su)
        self.assertEqual(self._hue("/admin/products/"), "indigo")
        self.assertEqual(self._hue("/admin/stores/"), "indigo")

    def test_dgc_is_orange(self):
        from apps.accounts.models import PlatformRole, Profile

        u = get_user_model().objects.create_user("dgc", "d@t.test", "pw", is_staff=True)
        Profile.objects.update_or_create(
            user=u, defaults={"platform_role": PlatformRole.MANAGER}
        )
        self._login(u)
        self.assertEqual(self._hue("/admin/stores/"), "orange")

    def test_store_owner_is_emerald_and_store_manager_is_rose(self):
        User = get_user_model()
        owner = User.objects.create_user("ow", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=self.project, role="owner")
        self._login(owner)
        self.assertEqual(self._hue(), "emerald")

        mgr = User.objects.create_user("mg", "m@t.test", "pw", is_staff=True)
        Membership.objects.create(user=mgr, project=self.project, role="manager")
        self._login(mgr)
        self.assertEqual(self._hue(), "rose")

    def test_django_admin_link_only_for_superuser(self):
        User = get_user_model()
        su = User.objects.create_superuser("root", "r@t.test", "pw")
        owner = User.objects.create_user("ow", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(user=owner, project=self.project, role="owner")

        self._login(su)
        self.assertContains(
            self.client.get("/admin/products/", HTTP_HOST="testserver", follow=True),
            "Django admin /sd/",
        )
        self._login(owner)
        self.assertNotContains(
            self.client.get("/admin/products/", HTTP_HOST="testserver", follow=True),
            "Django admin /sd/",
        )


class UserCreateTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="root", email="root@t.test", password="pw"
        )
        self.client.force_login(self.admin)

    def test_creates_plain_user_who_can_log_in(self):
        resp = self.client.post(
            "/admin/users/new/",
            {
                "email": "New@Shop.test", "first_name": "Nita", "last_name": "R",
                "platform_role": "none",
                "new_password1": "Zx9!kLmq7Ww", "new_password2": "Zx9!kLmq7Ww",
            },
        )
        self.assertEqual(resp.status_code, 302)
        user = get_user_model().objects.get(email="new@shop.test")
        self.assertTrue(user.check_password("Zx9!kLmq7Ww"))
        self.assertFalse(user.is_staff)  # no role yet
        self.assertTrue(self.client.login(username="new@shop.test", password="Zx9!kLmq7Ww"))

    def test_platform_role_grants_staff(self):
        self.client.post(
            "/admin/users/new/",
            {
                "email": "mgr@t.test", "platform_role": "platform_manager",
                "new_password1": "Zx9!kLmq7Ww", "new_password2": "Zx9!kLmq7Ww",
            },
        )
        user = get_user_model().objects.get(email="mgr@t.test")
        self.assertTrue(user.is_staff)
        self.assertEqual(user.profile.platform_role, "platform_manager")

    def test_duplicate_email_rejected(self):
        get_user_model().objects.create_user(username="dup", email="dup@t.test", password="x")
        resp = self.client.post(
            "/admin/users/new/",
            {
                "email": "dup@t.test", "platform_role": "none",
                "new_password1": "Zx9!kLmq7Ww", "new_password2": "Zx9!kLmq7Ww",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already exists")

    def test_non_admin_forbidden(self):
        owner = get_user_model().objects.create_user(
            username="ow", email="ow@t.test", password="pw", is_staff=True
        )
        self.client.force_login(owner)
        self.assertEqual(self.client.get("/admin/users/new/").status_code, 403)


class StoreCreateOwnerPasswordTests(TestCase):
    def setUp(self):
        from apps.billing.models import Plan

        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="root", email="root@t.test", password="pw"
        )
        self.plan = Plan.objects.filter(is_active=True).order_by("sort_order").first()
        self.client.force_login(self.admin)

    def _payload(self, **over):
        data = {
            "name": "Fresh Store", "primary_domain": "",
            "currency": "INR", "country": "IN",
            "owner_email": "owner@fresh.test", "owner_name": "Ola Owner",
            "plan": self.plan.pk, "period": "monthly",
        }
        data.update(over)
        return data

    def test_owner_password_lets_new_owner_log_in(self):
        resp = self.client.post(
            "/admin/stores/new/", self._payload(owner_password="Zx9!kLmq7Ww")
        )
        self.assertEqual(resp.status_code, 302)
        owner = get_user_model().objects.get(email="owner@fresh.test")
        self.assertTrue(owner.check_password("Zx9!kLmq7Ww"))
        self.assertTrue(self.client.login(username="owner@fresh.test", password="Zx9!kLmq7Ww"))

    def test_blank_owner_password_keeps_account_unusable(self):
        resp = self.client.post("/admin/stores/new/", self._payload())
        self.assertEqual(resp.status_code, 302)
        owner = get_user_model().objects.get(email="owner@fresh.test")
        self.assertFalse(owner.has_usable_password())

    def test_weak_owner_password_rejected(self):
        resp = self.client.post(
            "/admin/stores/new/", self._payload(owner_password="123")
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(email="owner@fresh.test").exists())


class UserSetPasswordTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="root", email="root@t.test", password="pw"
        )
        self.victim = User.objects.create_user(
            username="vic", email="vic@t.test", password="oldpass12345"
        )
        self.client.force_login(self.admin)

    def test_admin_sets_new_password(self):
        resp = self.client.post(
            f"/admin/users/{self.victim.pk}/set-password/",
            {"new_password1": "Zx9!kLmq7Ww", "new_password2": "Zx9!kLmq7Ww"},
        )
        self.assertEqual(resp.status_code, 302)
        self.victim.refresh_from_db()
        self.assertTrue(self.victim.check_password("Zx9!kLmq7Ww"))

    def test_weak_password_rejected(self):
        resp = self.client.post(
            f"/admin/users/{self.victim.pk}/set-password/",
            {"new_password1": "123", "new_password2": "123"},
        )
        self.assertEqual(resp.status_code, 200)
        self.victim.refresh_from_db()
        self.assertTrue(self.victim.check_password("oldpass12345"))

    def test_non_platform_admin_forbidden(self):
        owner = get_user_model().objects.create_user(
            username="ow", email="ow@t.test", password="pw", is_staff=True
        )
        self.client.force_login(owner)
        resp = self.client.get(f"/admin/users/{self.victim.pk}/set-password/")
        self.assertEqual(resp.status_code, 403)

    def test_platform_owner_cannot_reset_superuser(self):
        from apps.accounts.models import PlatformRole, Profile

        po = get_user_model().objects.create_user(
            username="po", email="po@t.test", password="pw", is_staff=True
        )
        Profile.objects.update_or_create(
            user=po, defaults={"platform_role": PlatformRole.OWNER}
        )
        self.client.force_login(po)
        resp = self.client.post(
            f"/admin/users/{self.admin.pk}/set-password/",
            {"new_password1": "Zx9!kLmq7Ww", "new_password2": "Zx9!kLmq7Ww"},
        )
        self.assertEqual(resp.status_code, 403)
