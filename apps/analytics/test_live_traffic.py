"""Visitors today / funnel / live-visitors dashboard widgets
(apps.analytics.services). See that module's "live traffic" section for why
these are cache/beacon-driven instead of counted from a Django view."""

import time
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.analytics.services import (
    classify_referrer,
    funnel_today,
    geoip_city,
    live_visitors,
    mark_visitor_seen,
    record_page_event,
    record_traffic_source,
    touch_live_visitor,
)
from apps.orders.models import Order
from apps.projects.models import Project


class ClassifyReferrerTests(TestCase):
    def test_no_referrer_is_direct(self):
        self.assertEqual(classify_referrer("", "shop.test"), "direct")

    def test_own_host_is_direct(self):
        self.assertEqual(classify_referrer("https://shop.test/cart/", "shop.test"), "direct")

    def test_search_engine(self):
        self.assertEqual(classify_referrer("https://www.google.com/search?q=x", "shop.test"), "search")

    def test_social(self):
        self.assertEqual(classify_referrer("https://www.instagram.com/", "shop.test"), "social")
        self.assertEqual(classify_referrer("https://l.instagram.com/", "shop.test"), "social")

    def test_other_site_is_referral(self):
        self.assertEqual(classify_referrer("https://someblog.example/post", "shop.test"), "referral")


class MarkVisitorSeenTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(name="TrafficCo", status="active")

    def test_first_beacon_of_day_is_new(self):
        self.assertTrue(mark_visitor_seen(self.project, "vid-1"))

    def test_second_beacon_same_day_not_new(self):
        mark_visitor_seen(self.project, "vid-1")
        self.assertFalse(mark_visitor_seen(self.project, "vid-1"))

    def test_different_visitor_is_new(self):
        mark_visitor_seen(self.project, "vid-1")
        self.assertTrue(mark_visitor_seen(self.project, "vid-2"))

    def test_new_visitor_bumps_visitor_counter(self):
        mark_visitor_seen(self.project, "vid-1")
        self.assertEqual(funnel_today(self.project)["visitors"], 1)
        mark_visitor_seen(self.project, "vid-1")  # repeat, not double-counted
        self.assertEqual(funnel_today(self.project)["visitors"], 1)


class RecordPageEventTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(name="PageCo", status="active")

    def test_page_view_only_bumps_page_views(self):
        record_page_event(self.project, "page_view")
        out = funnel_today(self.project)
        self.assertEqual(out["page_views"], 1)
        self.assertEqual(out["product_views"], 0)

    def test_product_view_bumps_both(self):
        record_page_event(self.project, "product_view")
        out = funnel_today(self.project)
        self.assertEqual(out["page_views"], 1)
        self.assertEqual(out["product_views"], 1)


class TrafficSourceRecordingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(name="SrcCo", status="active")

    def test_recorded_under_traffic_sources(self):
        record_traffic_source(self.project, "https://www.google.com/", "srcco.test")
        record_traffic_source(self.project, "", "srcco.test")
        out = funnel_today(self.project)
        self.assertEqual(out["traffic_sources"], {"search": 1, "direct": 1})


class LiveVisitorsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(name="LiveCo", status="active")

    def test_touch_then_list(self):
        touch_live_visitor(self.project, "vid-1", page="/", ip="")
        out = live_visitors(self.project)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["page"], "/")

    def test_second_touch_updates_not_duplicates(self):
        touch_live_visitor(self.project, "vid-1", page="/", ip="")
        touch_live_visitor(self.project, "vid-1", page="/cart/", ip="")
        out = live_visitors(self.project)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["page"], "/cart/")

    def test_stale_entry_expires(self):
        with patch("apps.analytics.services.time.time", return_value=1000.0):
            touch_live_visitor(self.project, "vid-1", page="/", ip="")
        with patch("apps.analytics.services.time.time", return_value=1000.0 + 9999):
            self.assertEqual(live_visitors(self.project), [])

    def test_scoped_per_project(self):
        other = Project.objects.create(name="OtherLiveCo", status="active")
        touch_live_visitor(self.project, "vid-1", page="/", ip="")
        self.assertEqual(live_visitors(other), [])


class GeoipCityGracefulTests(TestCase):
    def setUp(self):
        cache.clear()
        import apps.analytics.services as svc

        svc._geoip_reader = None
        svc._geoip_unavailable = False

    @override_settings(GEOIP_CITY_DB="/nonexistent/path/GeoLite2-City.mmdb",
                       GEOIP_EXTERNAL_LOOKUP=False)
    def test_missing_db_external_disabled_returns_blank_not_error(self):
        self.assertEqual(geoip_city("8.8.8.8"), "")

    @override_settings(GEOIP_CITY_DB="/nonexistent/path/GeoLite2-City.mmdb",
                       GEOIP_EXTERNAL_LOOKUP=True)
    def test_missing_db_external_enabled_never_raises(self):
        # No network in this test environment -- exercises the external
        # fallback's own try/except, not a specific result.
        self.assertIsInstance(geoip_city("8.8.8.8"), str)

    def test_blank_ip_returns_blank(self):
        self.assertEqual(geoip_city(""), "")

    @override_settings(GEOIP_CITY_DB="/nonexistent/path/GeoLite2-City.mmdb",
                       GEOIP_EXTERNAL_LOOKUP=False)
    def test_result_cached_per_ip(self):
        from unittest.mock import patch

        with patch("apps.analytics.services._geoip_local", return_value="Surat") as m:
            self.assertEqual(geoip_city("1.2.3.4"), "Surat")
            self.assertEqual(geoip_city("1.2.3.4"), "Surat")
            m.assert_called_once()


class FunnelTodayTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(name="FunnelCo", status="active", currency="INR")

    def test_conversion_rate_zero_with_no_visitors(self):
        out = funnel_today(self.project)
        self.assertEqual(out["conversion_rate"], 0.0)

    def test_conversion_rate_computed_from_orders_and_visitors(self):
        mark_visitor_seen(self.project, "v1")
        mark_visitor_seen(self.project, "v2")
        Order.objects.create(
            project=self.project, number="F1", email="b@t.test",
            subtotal="100", grand_total="100", status="confirmed",
        )
        out = funnel_today(self.project)
        self.assertEqual(out["visitors"], 2)
        self.assertEqual(out["orders"], 1)
        self.assertEqual(out["conversion_rate"], 50.0)

    def test_order_from_another_day_not_counted(self):
        from datetime import timedelta

        from django.utils import timezone

        o = Order.objects.create(
            project=self.project, number="F2", email="b@t.test",
            subtotal="100", grand_total="100", status="confirmed",
        )
        Order.objects.filter(pk=o.pk).update(created_at=timezone.now() - timedelta(days=2))
        self.assertEqual(funnel_today(self.project)["orders"], 0)
