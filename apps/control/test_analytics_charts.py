"""/admin/analytics/ now draws an inline SVG revenue line chart + an orders-
by-status donut (apps.analytics.services.revenue_chart_points /
status_donut_segments), instead of just numbers. Covers the chart-data
helpers and that the dashboard renders an <svg> even with zero orders."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.analytics.services import revenue_chart_points, status_donut_segments
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.orders.models import Order
from apps.projects.models import Project

User = get_user_model()


class RevenueChartPointsTests(TestCase):
    def test_empty_series_returns_empty_string(self):
        self.assertEqual(revenue_chart_points([]), "")

    def test_flat_zero_series_still_plots(self):
        series = [{"date": "2026-01-0%d" % i, "revenue": "0", "orders": 0} for i in range(1, 4)]
        pts = revenue_chart_points(series)
        self.assertEqual(len(pts.split(" ")), 3)

    def test_peak_maps_near_the_top(self):
        series = [
            {"date": "2026-01-01", "revenue": "0", "orders": 0},
            {"date": "2026-01-02", "revenue": "1000", "orders": 5},
        ]
        pts = revenue_chart_points(series).split(" ")
        _, y0 = pts[0].split(",")
        _, y1 = pts[1].split(",")
        self.assertLess(float(y1), float(y0))


class StatusDonutSegmentsTests(TestCase):
    def test_no_orders_returns_no_segments(self):
        self.assertEqual(status_donut_segments({"pending": 0, "delivered": 0}), [])

    def test_segments_sum_to_100_percent(self):
        segs = status_donut_segments({"pending": 1, "delivered": 3})
        self.assertAlmostEqual(sum(s["pct"] for s in segs), 100.0, places=1)

    def test_zero_count_statuses_excluded(self):
        segs = status_donut_segments({"pending": 0, "delivered": 2})
        self.assertEqual([s["status"] for s in segs], ["delivered"])


@override_settings(ALLOWED_HOSTS=["*"])
class AnalyticsDashboardChartRenderTests(TestCase):
    def setUp(self):
        self.store = Project.objects.create(
            name="ChartCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("co", "co@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.store.pk
        s.save()

    def test_dashboard_renders_svg_with_no_orders(self):
        resp = self.client.get("/admin/analytics/")
        self.assertContains(resp, "<svg")
        self.assertContains(resp, "No orders yet.")

    def test_dashboard_renders_donut_once_there_are_orders(self):
        Order.objects.create(
            project=self.store, number="AN-1", email="buyer@t.test",
            subtotal=Decimal("500"), grand_total=Decimal("500"), status="delivered",
        )
        resp = self.client.get("/admin/analytics/")
        self.assertContains(resp, "stroke-dasharray")
        self.assertNotContains(resp, "No orders yet.")
