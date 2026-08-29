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
