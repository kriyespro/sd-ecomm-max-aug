"""POST /beacon/ -- the storefront analytics beacon (static/shopfront/beacon.js
+ apps.shopfront.views.BeaconView). Drives apps.analytics.services' "Visitors
today" / funnel / live-visitors dashboard widgets."""

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.analytics.services import funnel_today, live_visitors
from apps.catalog.models import Product
from apps.projects.models import Domain, Project


@override_settings(ALLOWED_HOSTS=["*"], GEOIP_EXTERNAL_LOOKUP=False)
class BeaconViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(
            name="BeaconCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="beaconco.test", is_verified=True)
        self.product = Product.objects.create(
            project=self.project, title="Lamp", slug="lamp", status="active", price="499",
        )

    def _post(self, data):
        return self.client.post("/beacon/", data, HTTP_HOST="beaconco.test")

    def test_returns_204(self):
        resp = self._post({"kind": "view", "event": "page_view", "path": "/"})
        self.assertEqual(resp.status_code, 204)

    def test_sets_visitor_cookie_on_first_call(self):
        resp = self._post({"kind": "view", "event": "page_view", "path": "/"})
        self.assertIn("sd_vid", resp.cookies)

    def test_first_page_view_counts_as_a_visitor(self):
        self._post({"kind": "view", "event": "page_view", "path": "/"})
        self.assertEqual(funnel_today(self.project)["visitors"], 1)

    def test_repeat_beacon_same_browser_not_double_counted_as_visitor(self):
        resp = self._post({"kind": "view", "event": "page_view", "path": "/"})
        vid = resp.cookies["sd_vid"].value
        self.client.cookies["sd_vid"] = vid
        self._post({"kind": "view", "event": "page_view", "path": "/shop/"})
        self.assertEqual(funnel_today(self.project)["visitors"], 1)
        self.assertEqual(funnel_today(self.project)["page_views"], 2)

    def test_product_view_bumps_product_views(self):
        self._post({"kind": "view", "event": "product_view", "path": "/p/lamp/"})
        self.assertEqual(funnel_today(self.project)["product_views"], 1)

    def test_heartbeat_does_not_bump_page_views(self):
        resp = self._post({"kind": "view", "event": "page_view", "path": "/"})
        vid = resp.cookies["sd_vid"].value
        self.client.cookies["sd_vid"] = vid
        self._post({"kind": "heartbeat", "path": "/"})
        self.assertEqual(funnel_today(self.project)["page_views"], 1)

    def test_referrer_recorded_as_traffic_source_once_per_visitor(self):
        self._post({"kind": "view", "event": "page_view", "path": "/",
                    "referrer": "https://www.google.com/search"})
        self.assertEqual(funnel_today(self.project)["traffic_sources"], {"search": 1})

    def test_beacon_shows_up_in_live_visitors(self):
        self._post({"kind": "view", "event": "page_view", "path": "/shop/"})
        out = live_visitors(self.project)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["page"], "/shop/")

    def test_no_csrf_token_required(self):
        # csrf_exempt -- a fresh anonymous visitor has no CSRF cookie yet,
        # same constraint as the cart-mutation endpoints.
        from django.test import Client

        strict_client = Client(enforce_csrf_checks=True)
        resp = strict_client.post(
            "/beacon/", {"kind": "view", "event": "page_view", "path": "/"},
            HTTP_HOST="beaconco.test",
        )
        self.assertEqual(resp.status_code, 204)


@override_settings(ALLOWED_HOSTS=["*"])
class BeaconInjectionTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="InjectCo", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="injectco.test", is_verified=True)

    def test_beacon_script_tag_present_on_storefront_page(self):
        body = self.client.get("/", HTTP_HOST="injectco.test").content.decode()
        self.assertIn('/static/shopfront/beacon.js', body)

    def test_not_injected_into_htmx_fragment(self):
        body = self.client.get(
            "/cart/drawer/", HTTP_HOST="injectco.test", HTTP_HX_REQUEST="true",
        ).content.decode()
        self.assertNotIn("beacon.js", body)
