"""/admin/ (store_dashboard.jinja) now shows today's visitor funnel, a
traffic-source breakdown and a live-visitors widget (polled via
LiveVisitorsPartialView) alongside the existing Today numbers -- for the
store owner, and for a platform admin/DGC currently working inside that
store (same template, see apps.control.views.DashboardView)."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.analytics.services import mark_visitor_seen, touch_live_visitor
from apps.catalog.models import Product
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class StoreDashboardFunnelTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(
            name="DashFunnelCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("dfo", "dfo@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_funnel_section_renders_with_zero_state(self):
        resp = self.client.get("/admin/")
        self.assertContains(resp, "Today's funnel")
        self.assertContains(resp, "Traffic source")
        self.assertContains(resp, "No visits recorded yet today.")

    def test_funnel_reflects_a_visitor(self):
        mark_visitor_seen(self.project, "vid-1")
        resp = self.client.get("/admin/")
        body = resp.content.decode()
        funnel_idx = body.index("Today's funnel")
        self.assertIn("Visitors", body[funnel_idx:funnel_idx + 800])

    def test_live_visitors_placeholder_present(self):
        resp = self.client.get("/admin/")
        self.assertContains(resp, 'id="live-visitors"')
        self.assertContains(resp, "/admin/analytics/live-visitors/")


@override_settings(ALLOWED_HOSTS=["*"])
class LiveVisitorsPartialTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(
            name="LivePartialCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("lpo", "lpo@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_no_one_online(self):
        resp = self.client.get("/admin/analytics/live-visitors/")
        self.assertContains(resp, "No one browsing right now")

    def test_online_count_and_homepage_label(self):
        touch_live_visitor(self.project, "vid-1", page="/", ip="")
        resp = self.client.get("/admin/analytics/live-visitors/")
        self.assertContains(resp, "1 person online now")
        self.assertContains(resp, "Homepage")

    def test_product_page_resolved_to_title(self):
        Product.objects.create(
            project=self.project, title="Black T-Shirt", slug="black-tshirt",
            status="active", price="499",
        )
        touch_live_visitor(self.project, "vid-1", page="/p/black-tshirt/", ip="")
        resp = self.client.get("/admin/analytics/live-visitors/")
        self.assertContains(resp, "Product: Black T-Shirt")

    def test_multiple_visitors_plural_count(self):
        touch_live_visitor(self.project, "vid-1", page="/", ip="")
        touch_live_visitor(self.project, "vid-2", page="/shop/", ip="")
        resp = self.client.get("/admin/analytics/live-visitors/")
        self.assertContains(resp, "2 people online now")

    def test_scoped_to_active_project(self):
        other = Project.objects.create(name="OtherLivePartialCo", status="active")
        touch_live_visitor(other, "vid-1", page="/", ip="")
        resp = self.client.get("/admin/analytics/live-visitors/")
        self.assertContains(resp, "No one browsing right now")
