"""On-page guided tours + the show_guides master switch, and the
role-differentiated dashboard checklist (owner/manager gets 10 tracked
setup steps, staff/DGC get a short untracked orientation list)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole, UiMode
from apps.billing import services as billing_svc
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.control.tours import tour_for
from apps.projects.models import Project

User = get_user_model()


class TourRegistryTests(TestCase):
    def test_known_pages_have_tours(self):
        for name in ("dashboard", "product_list", "cms_store_profile",
                    "payment_providers", "shipping_zones", "order_list", "stores"):
            self.assertTrue(tour_for(name), name)

    def test_unknown_page_has_no_tour(self):
        self.assertIsNone(tour_for("some_random_url_name"))


@override_settings(ALLOWED_HOSTS=["*"])
class TourRenderingTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="TourCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)
        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_guides_on_by_default(self):
        self.assertTrue(self.owner.profile.show_guides)

    def test_tour_renders_on_a_page_that_has_one(self):
        resp = self.client.get("/admin/products/")
        self.assertContains(resp, "pageTour(")
        self.assertContains(resp, "product-new-btn")
        self.assertContains(resp, 'data-tour="product-new-btn"')

    def test_no_tour_markup_on_a_page_without_one(self):
        resp = self.client.get("/admin/inventory/")
        self.assertNotContains(resp, "pageTour(")

    def test_master_switch_off_suppresses_every_tour(self):
        Profile.objects.filter(user=self.owner).update(show_guides=False)
        resp = self.client.get("/admin/products/")
        self.assertNotContains(resp, "pageTour(")

    def test_toggle_flips_and_persists(self):
        resp = self.client.post("/admin/guides/toggle/", {"next": "/admin/products/"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.owner.profile.refresh_from_db()
        self.assertFalse(self.owner.profile.show_guides)
        self.assertNotContains(resp, "pageTour(")
        self.assertContains(resp, "Turn on page guides")

        self.client.post("/admin/guides/toggle/", {"next": "/admin/products/"})
        self.owner.profile.refresh_from_db()
        self.assertTrue(self.owner.profile.show_guides)

    def test_dashboard_tour_targets_present(self):
        Profile.objects.filter(user=self.owner).update(ui_mode=UiMode.EASY)
        resp = self.client.get("/admin/")
        self.assertContains(resp, 'data-tour="quick-launch"')
        self.assertContains(resp, 'data-tour="today-stats"')


@override_settings(ALLOWED_HOSTS=["*"])
class RoleChecklistTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="ChecklistCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(self.project)

    def _login_easy(self, user):
        self.client.force_login(user)
        Profile.objects.filter(user=user).update(ui_mode=UiMode.EASY)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_owner_gets_ten_tracked_steps(self):
        owner = User.objects.create_user("o2", "o2@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=owner, role=StoreRole.OWNER)
        self._login_easy(owner)
        resp = self.client.get("/admin/")
        self.assertContains(resp, "0 / 10")
        self.assertContains(resp, "Create a launch coupon")
        self.assertContains(resp, "Preview &amp; share your store")

    def test_staff_gets_short_untracked_list(self):
        staff = User.objects.create_user("s2", "s2@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=staff, role=StoreRole.STAFF)
        self._login_easy(staff)
        resp = self.client.get("/admin/")
        self.assertContains(resp, "Get oriented")
        self.assertContains(resp, "See today")  # "See today's orders" — apostrophe is HTML-escaped
        self.assertNotContains(resp, "/ 3")  # untracked — no progress count

    def test_dgc_without_membership_gets_platform_orientation(self):
        dgc = User.objects.create_user("d2", "d2@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=dgc).update(
            platform_role=PlatformRole.MANAGER, ui_mode=UiMode.EASY,
        )
        dgc = User.objects.get(pk=dgc.pk)
        sub = billing_svc.ensure_subscription(self.project)
        sub.manager = dgc
        sub.save(update_fields=["manager"])
        self.client.force_login(dgc)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        resp = self.client.get("/admin/")
        self.assertContains(resp, "Get oriented")
        self.assertContains(resp, "Check your commissions")

    def test_no_checklist_in_expert_mode(self):
        owner = User.objects.create_user("o3", "o3@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=owner, role=StoreRole.OWNER)
        self.client.force_login(owner)  # stays Expert (model default)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        resp = self.client.get("/admin/")
        self.assertNotContains(resp, "Get oriented")
        self.assertNotContains(resp, "Quick launch checklist")
