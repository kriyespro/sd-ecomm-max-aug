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
        self.a = Project.objects.create(
            name="StoreA", status="active", feature_flags={"onboarded": True}
        )
        self.b = Project.objects.create(
            name="StoreB", status="active", feature_flags={"onboarded": True}
        )
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
class TodayDashboardScreenTests(TestCase):
    """/admin/ shows a store's "Today" numbers once one is active — except for
    a pure DGC, who keeps seeing the platform-wide overview."""

    def setUp(self):
        from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY

        self.project = Project.objects.create(
            name="TodayCo", status="active", feature_flags={"onboarded": True}
        )
        self.owner = get_user_model().objects.create_user(
            username="tdo", email="tdo@t.test", password="pw", is_staff=True
        )
        Membership.objects.create(user=self.owner, project=self.project, role="owner")
        self._session_key = ACTIVE_PROJECT_SESSION_KEY

    def _login_with_store(self, user):
        self.client.force_login(user)
        s = self.client.session
        s[self._session_key] = self.project.pk
        s.save()

    def test_owner_sees_today_numbers(self):
        self._login_with_store(self.owner)
        resp = self.client.get("/admin/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Sales today")
        self.assertContains(resp, "Needs your attention")
        self.assertContains(resp, "Low stock")

    def test_manager_and_staff_also_see_it(self):
        for role in ("manager", "staff"):
            user = get_user_model().objects.create_user(
                username=f"td-{role}", email=f"td-{role}@t.test", password="pw", is_staff=True
            )
            Membership.objects.create(user=user, project=self.project, role=role)
            self._login_with_store(user)
            resp = self.client.get("/admin/")
            self.assertContains(resp, "Sales today")

    def test_dgc_still_sees_platform_overview_not_store_financials(self):
        from apps.accounts.models import PlatformRole, Profile
        from apps.billing import services as billing_svc

        billing_svc.ensure_subscription(self.project)
        dgc = get_user_model().objects.create_user(
            username="tddgc", email="tddgc@t.test", password="pw", is_staff=True
        )
        Profile.objects.filter(user=dgc).update(platform_role=PlatformRole.MANAGER)
        self.project.subscription.manager = dgc
        self.project.subscription.save(update_fields=["manager"])
        dgc = get_user_model().objects.get(pk=dgc.pk)

        self._login_with_store(dgc)
        resp = self.client.get("/admin/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Sales today")
        self.assertNotContains(resp, "Needs your attention")

    def test_owner_not_onboarded_is_sent_to_setup_wizard(self):
        unfinished = Project.objects.create(name="RawCo", status="active")  # onboarded not set
        Membership.objects.create(user=self.owner, project=unfinished, role="owner")
        self.client.force_login(self.owner)
        s = self.client.session
        s[self._session_key] = unfinished.pk
        s.save()
        resp = self.client.get("/admin/")
        self.assertRedirects(resp, "/admin/start/")

    def test_needs_attention_order_appears_and_links_to_detail(self):
        from apps.orders.models import Order

        order = Order.objects.create(
            project=self.project, number="TD-1", email="c@t.test",
            subtotal=0, discount_total=0, tax_total=0, shipping_total=0,
            grand_total=250, status="confirmed", payment_status="paid",
            fulfillment_status="unfulfilled",
        )
        self._login_with_store(self.owner)
        resp = self.client.get("/admin/")
        self.assertContains(resp, "TD-1")
        self.assertContains(resp, f"/admin/orders/{order.pk}/")


@override_settings(ALLOWED_HOSTS=["*"])
class ChromeThemeByRoleTests(TestCase):
    """Mission Control chrome colour follows the viewer's highest role:
    platform=indigo, DGC=orange, store owner=emerald, store manager=rose."""

    def setUp(self):
        self.project = Project.objects.create(
            name="TintCo", status="active", feature_flags={"onboarded": True}
        )
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

    def test_blank_owner_password_autogenerates_one(self):
        resp = self.client.post("/admin/stores/new/", self._payload(), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "one-time password")
        owner = get_user_model().objects.get(email="owner@fresh.test")
        self.assertTrue(owner.has_usable_password())

    def test_weak_owner_password_rejected(self):
        resp = self.client.post(
            "/admin/stores/new/", self._payload(owner_password="123")
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(email="owner@fresh.test").exists())


@override_settings(PLATFORM_HOSTS=["mnxstore.com"], PLATFORM_BASE_DOMAIN="mnxstore.com")
class StoreCreateSubdomainTests(TestCase):
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
            "name": "Fresh Store", "subdomain": "", "primary_domain": "",
            "currency": "INR", "country": "IN",
            "owner_email": "owner@fresh.test", "owner_name": "Ola Owner",
            "plan": self.plan.pk, "period": "monthly",
        }
        data.update(over)
        return data

    def test_typed_subdomain_is_assigned(self):
        resp = self.client.post("/admin/stores/new/", self._payload(subdomain="FreshPicks"))
        self.assertEqual(resp.status_code, 302)
        p = Project.objects.get(name="Fresh Store")
        self.assertEqual(p.primary_domain, "freshpicks.mnxstore.com")
        self.assertTrue(
            Domain.objects.filter(project=p, host="freshpicks.mnxstore.com",
                                  is_verified=True, is_primary=True).exists()
        )

    def test_blank_subdomain_falls_back_to_owner_email(self):
        resp = self.client.post("/admin/stores/new/", self._payload())
        self.assertEqual(resp.status_code, 302)
        p = Project.objects.get(name="Fresh Store")
        self.assertEqual(p.primary_domain, "owner.mnxstore.com")

    def test_taken_subdomain_is_rejected(self):
        other = Project.objects.create(name="Other")
        Domain.objects.create(project=other, host="taken.mnxstore.com",
                              is_verified=True, is_primary=True)
        resp = self.client.post("/admin/stores/new/", self._payload(subdomain="taken"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "taken")
        self.assertFalse(Project.objects.filter(name="Fresh Store").exists())

    def test_custom_domain_wins_over_subdomain(self):
        resp = self.client.post(
            "/admin/stores/new/",
            self._payload(subdomain="ignored", primary_domain="shop.brand.com"),
        )
        self.assertEqual(resp.status_code, 302)
        p = Project.objects.get(name="Fresh Store")
        self.assertEqual(p.primary_domain, "shop.brand.com")
        self.assertFalse(Domain.objects.filter(project=p, host__endswith=".mnxstore.com").exists())


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


class ImpersonateSelfTests(TestCase):
    def test_clicking_impersonate_on_your_own_row_shows_an_error_not_a_500(self):
        admin = get_user_model().objects.create_superuser(
            username="root5", email="root5@t.test", password="pw"
        )
        self.client.force_login(admin)
        resp = self.client.post(f"/admin/users/{admin.pk}/impersonate/", follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Not allowed to impersonate this user.")


class UserDeleteTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="root2", email="root2@t.test", password="pw"
        )
        self.victim = User.objects.create_user(
            username="vic2", email="vic2@t.test", password="pw"
        )
        self.client.force_login(self.admin)

    def test_admin_deletes_a_user(self):
        resp = self.client.post(f"/admin/users/{self.victim.pk}/delete/")
        self.assertRedirects(resp, "/admin/users/")
        self.assertFalse(get_user_model().objects.filter(pk=self.victim.pk).exists())

    def test_non_platform_admin_forbidden(self):
        owner = get_user_model().objects.create_user(
            username="ow2", email="ow2@t.test", password="pw", is_staff=True
        )
        self.client.force_login(owner)
        resp = self.client.post(f"/admin/users/{self.victim.pk}/delete/")
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(get_user_model().objects.filter(pk=self.victim.pk).exists())

    def test_cannot_delete_self(self):
        resp = self.client.post(f"/admin/users/{self.admin.pk}/delete/", follow=True)
        self.assertContains(resp, "cannot delete your own account")
        self.assertTrue(get_user_model().objects.filter(pk=self.admin.pk).exists())

    def test_cannot_delete_the_last_superuser(self):
        # Through the view this is unreachable except as a self-delete (the
        # only actor allowed to touch a superuser is one) — exercise the
        # service-level safety net directly, as defense in depth.
        from django.core.exceptions import PermissionDenied

        from apps.control import services

        with self.assertRaises(PermissionDenied):
            services.delete_user(actor=self.admin, target=self.admin, request=None)
        self.assertTrue(get_user_model().objects.filter(pk=self.admin.pk).exists())

    def test_non_superuser_platform_admin_cannot_delete_a_superuser(self):
        from apps.accounts.models import PlatformRole, Profile

        User = get_user_model()
        second_super = User.objects.create_superuser("root3", "root3@t.test", "pw")
        po = User.objects.create_user(username="po2", email="po2@t.test", password="pw", is_staff=True)
        Profile.objects.update_or_create(user=po, defaults={"platform_role": PlatformRole.OWNER})
        self.client.force_login(po)
        resp = self.client.post(f"/admin/users/{second_super.pk}/delete/")
        self.assertEqual(resp.status_code, 302)  # redirected back with an error message, not 403
        self.assertTrue(User.objects.filter(pk=second_super.pk).exists())

    def test_sole_store_owner_cannot_be_deleted(self):
        from apps.accounts.models import Membership
        from apps.projects.models import Project

        project = Project.objects.create(name="SoleCo", status="active")
        Membership.objects.create(project=project, user=self.victim, role="owner", is_active=True)
        resp = self.client.post(f"/admin/users/{self.victim.pk}/delete/", follow=True)
        self.assertContains(resp, "Reassign ownership first")
        self.assertTrue(get_user_model().objects.filter(pk=self.victim.pk).exists())

    def test_deletable_once_another_owner_exists(self):
        from apps.accounts.models import Membership
        from apps.projects.models import Project

        project = Project.objects.create(name="SharedCo", status="active")
        Membership.objects.create(project=project, user=self.victim, role="owner", is_active=True)
        other_owner = get_user_model().objects.create_user(
            username="oo", email="oo@t.test", password="pw"
        )
        Membership.objects.create(project=project, user=other_owner, role="owner", is_active=True)
        resp = self.client.post(f"/admin/users/{self.victim.pk}/delete/")
        self.assertRedirects(resp, "/admin/users/")
        self.assertFalse(get_user_model().objects.filter(pk=self.victim.pk).exists())

    def test_deleting_a_user_keeps_their_audit_trail(self):
        from apps.core.models import AuditLog

        self.client.post(f"/admin/users/{self.victim.pk}/delete/")
        log = AuditLog.objects.filter(action=AuditLog.Action.DELETE).latest("created_at")
        self.assertEqual(log.changes.get("email"), "vic2@t.test")
        self.assertEqual(log.changes.get("username"), "vic2")


class StoreOwnerTransferTests(TestCase):
    def setUp(self):
        from apps.accounts.models import Membership
        from apps.projects.models import Project

        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="root4", email="root4@t.test", password="pw"
        )
        self.old_owner = User.objects.create_user(
            username="oldowner", email="old@t.test", password="pw"
        )
        self.project = Project.objects.create(name="Kajal Store", status="active")
        self.membership = Membership.objects.create(
            project=self.project, user=self.old_owner, role="owner", is_active=True
        )
        self.client.force_login(self.admin)

    def test_transfer_to_new_email_creates_account_and_unblocks_delete(self):
        from apps.accounts.models import Membership

        resp = self.client.post(
            f"/admin/stores/{self.project.pk}/transfer-owner/",
            {"current_owner": self.old_owner.pk, "new_owner_email": "new@t.test"},
            follow=True,
        )
        self.assertContains(resp, "new@t.test is now the owner")
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.role, "manager")
        new_membership = Membership.objects.get(project=self.project, user__email="new@t.test")
        self.assertEqual(new_membership.role, "owner")
        self.assertTrue(new_membership.is_active)

        # the sole-owner block is now gone
        del_resp = self.client.post(f"/admin/users/{self.old_owner.pk}/delete/")
        self.assertRedirects(del_resp, "/admin/users/")

    def test_transfer_to_existing_team_member_promotes_them(self):
        from apps.accounts.models import Membership

        staff = get_user_model().objects.create_user(
            username="staff1", email="staff1@t.test", password="pw"
        )
        Membership.objects.create(
            project=self.project, user=staff, role="staff", is_active=True
        )
        self.client.post(
            f"/admin/stores/{self.project.pk}/transfer-owner/",
            {"current_owner": self.old_owner.pk, "new_owner_email": "staff1@t.test"},
        )
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.role, "manager")
        promoted = Membership.objects.get(project=self.project, user=staff)
        self.assertEqual(promoted.role, "owner")

    def test_non_platform_admin_forbidden(self):
        owner = get_user_model().objects.create_user(
            username="ow3", email="ow3@t.test", password="pw", is_staff=True
        )
        self.client.force_login(owner)
        resp = self.client.post(
            f"/admin/stores/{self.project.pk}/transfer-owner/",
            {"current_owner": self.old_owner.pk, "new_owner_email": "new@t.test"},
        )
        self.assertEqual(resp.status_code, 403)


class PartnerApplicationReviewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")
        from apps.accounts.models import PartnerApplication
        self.app = PartnerApplication.objects.create(
            full_name="Ravi Partner", email="ravi@x.test",
            audience="Runs a seller community.",
        )

    def test_list_page_renders(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/admin/partners/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ravi@x.test")

    def test_approve_creates_dgc_login(self):
        self.client.force_login(self.admin)
        resp = self.client.post(f"/admin/partners/{self.app.pk}/review/", {"decision": "approve"})
        self.assertEqual(resp.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "approved")
        self.assertIsNotNone(self.app.created_user)
        self.assertEqual(self.app.created_user.profile.platform_role, "platform_manager")
        self.assertTrue(self.app.created_user.is_staff)

    def test_reject_sets_status_and_creates_no_user(self):
        self.client.force_login(self.admin)
        resp = self.client.post(f"/admin/partners/{self.app.pk}/review/", {"decision": "reject"})
        self.assertEqual(resp.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "rejected")
        self.assertIsNone(self.app.created_user)

    def test_non_admin_cannot_review(self):
        staff = get_user_model().objects.create_user("s", "s@t.test", "pw", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.post(f"/admin/partners/{self.app.pk}/review/", {"decision": "approve"})
        self.assertIn(resp.status_code, (302, 403))
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "pending")


class ProductDuplicateTests(TestCase):
    def setUp(self):
        from apps.catalog.models import Product

        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="pdup", email="pdup@t.test", password="pw"
        )
        self.project = Project.objects.create(name="DupStore", status="active")
        self.client.force_login(self.user)
        session = self.client.session
        session[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        session.save()
        self.product = Product.objects.create(
            project=self.project, title="Original", price="199.00", status="active",
            search_indexed=True,
        )

    def test_form_shows_duplicate_and_add_another(self):
        resp = self.client.get(f"/admin/products/{self.product.pk}/")
        self.assertContains(resp, "Duplicate")
        self.assertContains(resp, "Add another product")

    def test_duplicate_creates_draft_copy_and_redirects_to_it(self):
        from apps.catalog.models import Product

        resp = self.client.post(f"/admin/products/{self.product.pk}/duplicate/")
        self.assertEqual(resp.status_code, 302)
        clone = Product.objects.exclude(pk=self.product.pk).get(project=self.project)
        self.assertEqual(resp["Location"], f"/admin/products/{clone.pk}/")
        self.assertEqual(clone.title, "Original (copy)")
        self.assertEqual(clone.status, "draft")
        # search_indexed is inherited (not forced off): DRAFT hides the copy, and
        # forcing it off used to leave the product invisible after activation.
        self.assertEqual(clone.search_indexed, self.product.search_indexed)
        self.assertNotEqual(clone.slug, self.product.slug)
        self.assertNotEqual(clone.sku, self.product.sku)

    def test_price_field_follows_kind_in_form_order(self):
        from apps.control.forms import ProductForm

        order = list(ProductForm.base_fields)
        self.assertEqual(order.index("price"), order.index("kind") + 1)


@override_settings(ALLOWED_HOSTS=["*"])
class PaymentProviderFormTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser("payboss", "pay@t.test", "pw")
        self.project = Project.objects.create(name="PayStore", status="active",
                                              feature_flags={"onboarded": True})
        self.client.force_login(self.user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def _post(self, **extra):
        data = {
            "provider": "razorpay", "display_name": "Razorpay",
            "priority": "100", "is_test_mode": "on",
            "key_id": "rzp_test_abc", "key_secret": "s3cr3t",
        }
        data.update(extra)
        return self.client.post("/admin/payments/providers/new/", data)

    def test_form_has_typed_key_fields_and_no_raw_json(self):
        from apps.control.forms import PaymentProviderForm
        fields = set(PaymentProviderForm.base_fields)
        self.assertIn("key_id", fields)
        self.assertIn("key_secret", fields)
        self.assertNotIn("webhook_secret", fields)
        self.assertNotIn("credentials", fields)
        self.assertNotIn("config", fields)

    def test_create_packs_keys_into_credentials(self):
        from apps.payments.models import PaymentProviderConfig
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        cfg = PaymentProviderConfig.objects.get(project=self.project, provider="razorpay")
        self.assertEqual(cfg.credentials, {"key_id": "rzp_test_abc", "key_secret": "s3cr3t"})
        self.assertEqual(cfg.config, {})

    def test_existing_webhook_secret_is_preserved(self):
        from apps.payments.models import PaymentProviderConfig
        cfg = PaymentProviderConfig.objects.create(
            project=self.project, provider="razorpay",
            credentials={"key_id": "old", "key_secret": "old", "webhook_secret": "keep"},
        )
        resp = self.client.post(f"/admin/payments/providers/{cfg.pk}/", {
            "provider": "razorpay", "display_name": "Razorpay", "priority": "100",
            "is_test_mode": "on", "key_id": "new_id", "key_secret": "new_secret",
        })
        self.assertEqual(resp.status_code, 302)
        cfg.refresh_from_db()
        self.assertEqual(cfg.credentials, {
            "key_id": "new_id", "key_secret": "new_secret", "webhook_secret": "keep",
        })

    def test_duplicate_provider_is_a_form_error_not_500(self):
        from apps.payments.models import PaymentProviderConfig
        PaymentProviderConfig.objects.create(project=self.project, provider="razorpay")
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already set up")
        self.assertEqual(
            PaymentProviderConfig.objects.filter(project=self.project, provider="razorpay").count(), 1
        )

    def test_enable_without_keys_is_rejected(self):
        resp = self._post(is_enabled="on", key_id="", key_secret="")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "before enabling Razorpay")

    def test_edit_prefills_key_fields_from_credentials(self):
        from apps.payments.models import PaymentProviderConfig
        cfg = PaymentProviderConfig.objects.create(
            project=self.project, provider="razorpay",
            credentials={"key_id": "rzp_live_x", "key_secret": "s"},
        )
        resp = self.client.get(f"/admin/payments/providers/{cfg.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "rzp_live_x")

    def test_edit_page_never_echoes_the_saved_key_secret(self):
        from apps.payments.models import PaymentProviderConfig
        cfg = PaymentProviderConfig.objects.create(
            project=self.project, provider="razorpay",
            credentials={"key_id": "rzp_live_x", "key_secret": "top-secret-value"},
        )
        resp = self.client.get(f"/admin/payments/providers/{cfg.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "top-secret-value")

    def test_leaving_key_secret_blank_on_edit_keeps_the_existing_one(self):
        from apps.payments.models import PaymentProviderConfig
        cfg = PaymentProviderConfig.objects.create(
            project=self.project, provider="razorpay", is_enabled=True,
            credentials={"key_id": "rzp_live_x", "key_secret": "keep-me"},
        )
        resp = self.client.post(f"/admin/payments/providers/{cfg.pk}/", {
            "provider": "razorpay", "display_name": "Razorpay", "priority": "100",
            "is_enabled": "on", "key_id": "rzp_live_y", "key_secret": "",
        })
        self.assertEqual(resp.status_code, 302)
        cfg.refresh_from_db()
        self.assertEqual(cfg.credentials["key_secret"], "keep-me")
        self.assertEqual(cfg.credentials["key_id"], "rzp_live_y")
