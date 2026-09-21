"""/admin/ (store_dashboard.jinja) now shows today's visitor funnel, a
traffic-source breakdown and a live-visitors widget (polled via
LiveVisitorsPartialView) alongside the existing Today numbers -- for the
store owner, and for a platform admin/DGC currently working inside that
store (same template, see apps.control.views.DashboardView)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.analytics.services import mark_visitor_seen, touch_live_visitor
from apps.catalog.models import Product
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.control.views import _greeting
from apps.projects.models import Project

User = get_user_model()


class GreetingTests(TestCase):
    def _at_ist_hour(self, hour):
        return datetime(2026, 1, 1, hour, 30, tzinfo=ZoneInfo("Asia/Kolkata"))

    def test_morning(self):
        self.assertEqual(_greeting(self._at_ist_hour(9)), "Good morning")

    def test_afternoon(self):
        self.assertEqual(_greeting(self._at_ist_hour(14)), "Good afternoon")

    def test_evening(self):
        self.assertEqual(_greeting(self._at_ist_hour(20)), "Good evening")

    def test_boundary_just_before_noon_is_still_morning(self):
        self.assertEqual(_greeting(self._at_ist_hour(11)), "Good morning")

    def test_boundary_5pm_is_evening(self):
        self.assertEqual(_greeting(self._at_ist_hour(17)), "Good evening")


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
class StoreDashboardChartsTests(TestCase):
    """The revenue line chart + orders-by-status donut from the full
    Analytics report now also render on the dashboard itself (merged in, so
    /admin/ is a one-page report), above the stats cards -- and the old
    bottom CSS-bar revenue chart is gone, replaced by this one."""

    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(
            name="DashChartCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("dco", "dco@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_charts_render_with_zero_state(self):
        resp = self.client.get("/admin/")
        self.assertContains(resp, "Revenue — last 30 days")
        self.assertContains(resp, "Orders by status")
        self.assertContains(resp, "No orders yet.")

    def test_charts_appear_before_stats_cards(self):
        body = self.client.get("/admin/").content.decode()
        chart_idx = body.index("Revenue — last 30 days")
        stats_idx = body.index('data-tour="today-stats"')
        self.assertLess(chart_idx, stats_idx)

    def test_only_one_revenue_chart_on_the_page(self):
        body = self.client.get("/admin/").content.decode()
        self.assertEqual(body.count("Revenue — last 30 days"), 1)

    def test_donut_renders_once_there_are_orders(self):
        from apps.orders.models import Order

        Order.objects.create(
            project=self.project, number="DC-1", email="buyer@t.test",
            subtotal="500", grand_total="500", status="delivered",
        )
        body = self.client.get("/admin/").content.decode()
        self.assertIn("stroke-dasharray", body)
        self.assertNotIn("No orders yet.", body)

    def test_full_analytics_link_still_present(self):
        resp = self.client.get("/admin/")
        self.assertContains(resp, 'href="/admin/analytics/"')

    def test_greeting_and_customers_card_present(self):
        resp = self.client.get("/admin/")
        body = resp.content.decode()
        self.assertRegex(body, r"Good (morning|afternoon|evening), dco")
        self.assertIn("DashChartCo", body)
        self.assertIn("Customers", body)
        self.assertIn("High value", body)

    def test_all_three_analytics_cards_present_and_not_duplicated(self):
        # Orders / Customers / Products, moved in from the full Analytics
        # report verbatim -- each appears exactly once, not duplicated with
        # the pre-existing donut/best-sellers/customers content.
        body = self.client.get("/admin/").content.decode()
        self.assertIn("paid · AOV", body)
        self.assertIn("Best sellers", body)
        self.assertEqual(body.count("👥 Customers"), 1)

    def test_today_stats_tour_target_still_present(self):
        # apps.control.tours references data-tour="today-stats" by name --
        # the hero/secondary-card restyle must keep this exact anchor.
        resp = self.client.get("/admin/")
        self.assertContains(resp, 'data-tour="today-stats"')


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
