"""Meta Pixel + Conversions API tracking."""

import hashlib
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.billing import services as billing_svc
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.core.events import Events, emit
from apps.marketing import capi, ga4, providers, tiktok
from apps.marketing.context_processors import platform_pixels
from apps.marketing.models import (
    PlatformTrackingSettings,
    TrackingIntegration,
    TrackingProvider,
    platform_tracking,
    tracking_for,
)
from apps.marketing.tasks import (
    send_ga4_event,
    send_meta_capi_event,
    send_tiktok_event,
)
from apps.projects.models import Project
from apps.shopfront.middleware import TrackingInjectionMiddleware

User = get_user_model()


def _meta(project, **kw):
    defaults = dict(
        provider=TrackingProvider.META,
        pixel_id="123456789012345",
        server_token="tok",
        track_browser=True,
        track_server=True,
        is_enabled=True,
    )
    defaults.update(kw)
    return TrackingIntegration.objects.create(project=project, **defaults)


def _ga4(project, **kw):
    defaults = dict(provider=TrackingProvider.GA4, pixel_id="G-ABC1234",
                    server_token="secret", is_enabled=True)
    defaults.update(kw)
    return TrackingIntegration.objects.create(project=project, **defaults)


def _tiktok(project, **kw):
    defaults = dict(provider=TrackingProvider.TIKTOK, pixel_id="C4A1B2C3D4E5F6G7H8",
                    server_token="tttoken", is_enabled=True)
    defaults.update(kw)
    return TrackingIntegration.objects.create(project=project, **defaults)


class ModelReadinessTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Shop", status="active", currency="INR")

    def test_browser_ready_needs_pixel_and_enabled(self):
        row = _meta(self.project, server_token="")
        self.assertTrue(row.browser_ready)
        self.assertFalse(row.server_ready)

    def test_server_ready_needs_token(self):
        self.assertTrue(_meta(self.project).server_ready)

    def test_disabled_is_never_ready(self):
        row = _meta(self.project, is_enabled=False)
        self.assertFalse(row.browser_ready)
        self.assertFalse(row.server_ready)

    def test_track_toggles_gate_each_channel(self):
        row = _meta(self.project, track_browser=False, track_server=False)
        self.assertFalse(row.browser_ready)
        self.assertFalse(row.server_ready)


class TrackingForCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(name="Shop", status="active", currency="INR")

    def test_returns_browser_fields_only(self):
        _meta(self.project)
        data = tracking_for(self.project)
        self.assertEqual(data, {"meta": {"pixel_id": "123456789012345"}})
        self.assertNotIn("server_token", data["meta"])

    def test_save_busts_cache(self):
        row = _meta(self.project)
        self.assertIn("meta", tracking_for(self.project))
        row.is_enabled = False
        row.save()
        self.assertEqual(tracking_for(self.project), {})

    def test_none_project(self):
        self.assertEqual(tracking_for(None), {})


class HashUserDataTests(TestCase):
    def test_email_lowercased_trimmed_sha256(self):
        out = capi.hash_user_data(email="  Buyer@Example.com ")
        self.assertEqual(
            out["em"], [hashlib.sha256(b"buyer@example.com").hexdigest()]
        )

    def test_phone_digits_only(self):
        out = capi.hash_user_data(phone="+91 (98) 765-43210")
        self.assertEqual(out["ph"], [hashlib.sha256(b"919876543210").hexdigest()])

    def test_raw_fields_not_hashed(self):
        out = capi.hash_user_data(client_ip="1.2.3.4", user_agent="UA", fbp="fb.1.2.3")
        self.assertEqual(out["client_ip_address"], "1.2.3.4")
        self.assertEqual(out["client_user_agent"], "UA")
        self.assertEqual(out["fbp"], "fb.1.2.3")

    def test_empty_input_empty_output(self):
        self.assertEqual(capi.hash_user_data(), {})


class CapiTaskTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Shop", status="active", currency="INR")

    def test_skips_when_not_server_ready(self):
        row = _meta(self.project, server_token="")
        with mock.patch.object(capi, "send_event") as sent:
            result = send_meta_capi_event(
                integration_id=row.pk, event_name="Purchase",
                event_id="1", user_data={},
            )
        self.assertEqual(result, "skipped")
        sent.assert_not_called()

    def test_sends_when_ready(self):
        row = _meta(self.project, test_event_code="TEST123")
        with mock.patch.object(capi, "send_event", return_value={"events_received": 1}) as sent:
            result = send_meta_capi_event(
                integration_id=row.pk, event_name="Purchase",
                event_id="ord-1", user_data={"em": ["x"]},
                custom_data={"value": 10.0, "currency": "INR"},
            )
        self.assertEqual(result, "sent")
        _, kwargs = sent.call_args
        self.assertEqual(kwargs["pixel_id"], "123456789012345")
        self.assertEqual(kwargs["access_token"], "tok")
        self.assertEqual(kwargs["event_id"], "ord-1")
        self.assertEqual(kwargs["test_event_code"], "TEST123")


class PaymentSuccessReceiverTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Shop", status="active", currency="INR")

    def _payment(self, number="A1001"):
        order = mock.Mock(number=number, email="b@t.test", phone="", currency="INR",
                          grand_total=Decimal("500"), customer_id=7,
                          shipping_address={"city": "Pune"})
        order.items.all.return_value = []
        payment = mock.Mock(order=order)
        return payment

    def _emit(self, payment, number="A1001"):
        payload = {"order_number": number, "email": "b@t.test",
                   "currency": "INR", "total": "500.00"}
        emit(Events.PAYMENT_SUCCESS, project=self.project, payload=payload, instance=payment)

    def test_enqueues_purchase_when_server_ready(self):
        row = _meta(self.project)
        with mock.patch("apps.marketing.tasks.send_meta_capi_event.delay") as delay:
            self._emit(self._payment())
        delay.assert_called_once()
        kwargs = delay.call_args.kwargs
        self.assertEqual(kwargs["integration_id"], row.pk)
        self.assertEqual(kwargs["event_name"], "Purchase")
        self.assertEqual(kwargs["event_id"], "A1001")

    def test_no_enqueue_without_server_token(self):
        _meta(self.project, server_token="")
        with mock.patch("apps.marketing.tasks.send_meta_capi_event.delay") as delay:
            self._emit(self._payment("A1002"), number="A1002")
        delay.assert_not_called()

    def test_other_events_ignored(self):
        _meta(self.project)
        with mock.patch("apps.marketing.tasks.send_meta_capi_event.delay") as delay:
            emit(Events.ORDER_CREATED, project=self.project, payload={},
                 instance=self._payment())
        delay.assert_not_called()


class MiddlewareInjectionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(name="Shop", status="active", currency="INR")

    def _run(self, *, html="<html><head></head><body>hi</body></html>", tracking=None,
             path="/app/", status=200, ctype="text/html; charset=utf-8"):
        req = RequestFactory().get(path)
        req.project = self.project
        req.user = AnonymousUser()
        if tracking is not None:
            req._tracking = tracking

        def get_response(r):
            resp = HttpResponse(html, status=status)
            resp["Content-Type"] = ctype
            return resp

        return TrackingInjectionMiddleware(get_response)(req)

    def test_injects_base_pixel_when_enabled(self):
        _meta(self.project)
        body = self._run().content.decode()
        self.assertIn("fbevents.js", body)
        self.assertIn("fbq('init','123456789012345')", body)

    def test_injects_per_page_event(self):
        _meta(self.project)
        body = self._run(tracking=("Purchase", {"value": 10}, "ord-9")).content.decode()
        self.assertIn('fbq(\'track\',"Purchase"', body)
        self.assertIn("ord-9", body)

    def test_noop_when_no_integration(self):
        body = self._run().content.decode()
        self.assertNotIn("fbevents.js", body)

    def test_noop_when_disabled(self):
        _meta(self.project, is_enabled=False)
        body = self._run().content.decode()
        self.assertNotIn("fbevents.js", body)

    def test_noop_for_non_html(self):
        _meta(self.project)
        body = self._run(ctype="application/json").content.decode()
        self.assertNotIn("fbevents.js", body)

    def test_noop_for_htmx(self):
        _meta(self.project)
        req = RequestFactory().get("/app/", HTTP_HX_REQUEST="true")
        req.project = self.project
        req.user = AnonymousUser()
        out = TrackingInjectionMiddleware(
            lambda r: self._html_response()
        )(req)
        self.assertNotIn("fbevents.js", out.content.decode())

    def _html_response(self):
        resp = HttpResponse("<html><head></head><body>x</body></html>")
        resp["Content-Type"] = "text/html; charset=utf-8"
        return resp


@override_settings(ALLOWED_HOSTS=["*"])
class AdminScreenAccessTests(TestCase):
    def setUp(self):
        self.store = Project.objects.create(name="ShopCo", status="active",
                                            feature_flags={"onboarded": True})
        self.sub = billing_svc.ensure_subscription(self.store)

        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.owner, role=StoreRole.OWNER)
        self.manager = User.objects.create_user("m", "m@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.manager, role=StoreRole.MANAGER)
        self.staff = User.objects.create_user("s", "s@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.staff, role=StoreRole.STAFF)

        self.dgc = User.objects.create_user("d", "d@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=self.dgc).update(platform_role=PlatformRole.MANAGER)
        self.sub.manager = self.dgc
        self.sub.save(update_fields=["manager"])
        self.dgc = User.objects.get(pk=self.dgc.pk)

    def _login(self, user):
        self.client.force_login(user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.store.pk
        s.save()

    def test_owner_manager_dgc_allowed(self):
        for user in (self.owner, self.manager, self.dgc):
            self._login(user)
            self.assertEqual(self.client.get("/admin/marketing/tracking/").status_code, 200,
                             msg=user.username)

    def test_plain_staff_denied(self):
        self._login(self.staff)
        self.assertEqual(self.client.get("/admin/marketing/tracking/").status_code, 403)

    def test_dgc_can_save_pixel(self):
        self._login(self.dgc)
        resp = self.client.post("/admin/marketing/tracking/new/", {
            "provider": TrackingProvider.META,
            "pixel_id": "998877665544332",
            "server_token": "captoken",
            "test_event_code": "",
            "track_browser": "on",
            "track_server": "on",
            "is_enabled": "on",
        })
        self.assertEqual(resp.status_code, 302)
        row = TrackingIntegration.objects.get(project=self.store)
        self.assertEqual(row.pixel_id, "998877665544332")
        self.assertTrue(row.server_ready)

    def test_pixel_id_must_be_numeric(self):
        self._login(self.owner)
        resp = self.client.post("/admin/marketing/tracking/new/", {
            "provider": TrackingProvider.META,
            "pixel_id": "not-a-number",
            "server_token": "",
            "test_event_code": "",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(TrackingIntegration.objects.filter(project=self.store).exists())

    def test_blank_token_on_edit_keeps_stored(self):
        row = _meta(self.store)
        self._login(self.owner)
        resp = self.client.post(f"/admin/marketing/tracking/{row.pk}/", {
            "pixel_id": "123456789012345",
            "server_token": "",
            "test_event_code": "",
            "track_browser": "on",
            "track_server": "on",
            "is_enabled": "on",
        })
        self.assertEqual(resp.status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.server_token, "tok")

    def test_can_add_ga4(self):
        self._login(self.owner)
        resp = self.client.post("/admin/marketing/tracking/new/", {
            "provider": TrackingProvider.GA4,
            "pixel_id": "G-ABC1234",
            "server_token": "apisecret",
            "test_event_code": "",
            "track_browser": "on",
            "track_server": "on",
            "is_enabled": "on",
        })
        self.assertEqual(resp.status_code, 302)
        row = TrackingIntegration.objects.get(project=self.store, provider=TrackingProvider.GA4)
        self.assertTrue(row.server_ready)

    def test_ga4_id_format_validated(self):
        self._login(self.owner)
        resp = self.client.post("/admin/marketing/tracking/new/", {
            "provider": TrackingProvider.GA4,
            "pixel_id": "12345",          # Meta-style, wrong for GA4
            "server_token": "",
            "test_event_code": "",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            TrackingIntegration.objects.filter(project=self.store, provider=TrackingProvider.GA4).exists()
        )


class ProviderSnippetTests(TestCase):
    def test_meta_head_and_event(self):
        head = providers.head_snippet("meta", {"pixel_id": "111222333"})
        self.assertIn("fbevents.js", head)
        self.assertIn("fbq('init','111222333')", head)
        evt = providers.event_snippet("meta", {}, "Purchase", {"value": 5}, "o1")
        self.assertIn('fbq(\'track\',"Purchase"', evt)
        self.assertIn('"eventID":"o1"', evt)

    def test_ga4_head_and_event(self):
        head = providers.head_snippet("ga4", {"pixel_id": "G-XYZ999"})
        self.assertIn("googletagmanager.com/gtag/js?id=G-XYZ999", head)
        self.assertIn("gtag('config','G-XYZ999')", head)
        evt = providers.event_snippet("ga4", {}, "Purchase",
                                      {"value": 5, "currency": "INR", "order_id": "A9"}, "A9")
        self.assertIn("gtag('event',\"purchase\"", evt)
        self.assertIn('"transaction_id":"A9"', evt)

    def test_ga4_maps_event_names(self):
        self.assertIn("view_item",
                      providers.event_snippet("ga4", {}, "ViewContent", {}, None))
        self.assertIn("begin_checkout",
                      providers.event_snippet("ga4", {}, "InitiateCheckout", {}, None))

    def test_tiktok_head_and_event(self):
        head = providers.head_snippet("tiktok", {"pixel_id": "CABC123"})
        self.assertIn("analytics.tiktok.com", head)
        self.assertIn('ttq.load("CABC123")', head)
        evt = providers.event_snippet("tiktok", {}, "Purchase",
                                      {"value": 5, "currency": "INR",
                                       "content_ids": ["SKU1"]}, "o2")
        self.assertIn('ttq.track("CompletePayment"', evt)
        self.assertIn('"event_id":"o2"', evt)
        self.assertIn('"content_id":"SKU1"', evt)

    def test_pageview_never_an_event(self):
        for p in ("meta", "ga4", "tiktok"):
            self.assertEqual(providers.event_snippet(p, {}, "PageView", {}, None), "")


class GA4TaskTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Shop", status="active", currency="INR")

    def test_client_id_stable(self):
        self.assertEqual(ga4.client_id_for("A1"), ga4.client_id_for("A1"))
        self.assertNotEqual(ga4.client_id_for("A1"), ga4.client_id_for("A2"))

    def test_task_skips_without_token(self):
        row = _ga4(self.project, server_token="")
        with mock.patch.object(ga4, "send_event") as sent:
            self.assertEqual(
                send_ga4_event(integration_id=row.pk, name="purchase",
                               event_id="A1", params={}), "skipped")
        sent.assert_not_called()

    def test_task_sends(self):
        row = _ga4(self.project)
        with mock.patch.object(ga4, "send_event") as sent:
            self.assertEqual(
                send_ga4_event(integration_id=row.pk, name="purchase",
                               event_id="A1", params={"value": 9}), "sent")
        _, kw = sent.call_args
        self.assertEqual(kw["measurement_id"], "G-ABC1234")
        self.assertEqual(kw["api_secret"], "secret")


class TikTokTaskTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Shop", status="active", currency="INR")

    def test_user_pii_hashed(self):
        import json

        captured = {}

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"code":0}'

        def fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode())
            return _Resp()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            tiktok.send_event(pixel_code="C1", access_token="t", event_name="CompletePayment",
                              event_id="o1", contact={"email": "A@B.com", "phone": "+91 98765 43210"},
                              properties={"value": 1})
        user = captured["body"]["data"][0]["user"]
        self.assertEqual(user["email"], hashlib.sha256(b"a@b.com").hexdigest())
        self.assertEqual(user["phone"], hashlib.sha256(b"+919876543210").hexdigest())

    def test_task_skips_without_token(self):
        row = _tiktok(self.project, server_token="")
        with mock.patch.object(tiktok, "send_event") as sent:
            self.assertEqual(
                send_tiktok_event(integration_id=row.pk, event_name="CompletePayment",
                                  event_id="o1", contact={}, properties={}), "skipped")
        sent.assert_not_called()

    def test_nonzero_code_raises(self):
        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"code":40000,"message":"bad"}'

        with mock.patch("urllib.request.urlopen", lambda *a, **k: _Resp()):
            with self.assertRaises(tiktok.TikTokError):
                tiktok.send_event(pixel_code="C1", access_token="t",
                                  event_name="X", event_id="1", contact={})


class MultiProviderFanOutTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(name="Shop", status="active", currency="INR")

    def _payment(self):
        order = mock.Mock(number="Z9", email="b@t.test", phone="", currency="INR",
                          grand_total=Decimal("300"), customer_id=3,
                          shipping_address={"city": "Pune"})
        order.items.all.return_value = []
        return mock.Mock(order=order)

    def test_all_three_enqueue_on_payment_success(self):
        _meta(self.project)
        _ga4(self.project)
        _tiktok(self.project)
        with mock.patch("apps.marketing.tasks.send_meta_capi_event.delay") as m, \
             mock.patch("apps.marketing.tasks.send_ga4_event.delay") as g, \
             mock.patch("apps.marketing.tasks.send_tiktok_event.delay") as t:
            emit(Events.PAYMENT_SUCCESS, project=self.project,
                 payload={"order_number": "Z9", "email": "b@t.test",
                          "currency": "INR", "total": "300.00"},
                 instance=self._payment())
        m.assert_called_once()
        g.assert_called_once()
        t.assert_called_once()
        self.assertEqual(g.call_args.kwargs["name"], "purchase")
        self.assertEqual(t.call_args.kwargs["event_name"], "CompletePayment")

    def test_middleware_injects_all_enabled(self):
        _meta(self.project)
        _ga4(self.project)
        req = RequestFactory().get("/app/")
        req.project = self.project
        req.user = AnonymousUser()

        def get_response(r):
            resp = HttpResponse("<html><head></head><body>x</body></html>")
            resp["Content-Type"] = "text/html; charset=utf-8"
            return resp

        body = TrackingInjectionMiddleware(get_response)(req).content.decode()
        self.assertIn("fbevents.js", body)
        self.assertIn("googletagmanager.com/gtag/js", body)


@override_settings(ALLOWED_HOSTS=["*"])
class PlatformTrackingTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_as_map_empty_when_disabled(self):
        s = PlatformTrackingSettings.load()
        s.meta_pixel_id = "123456789"
        s.is_enabled = False
        s.save()
        self.assertEqual(s.as_map(), {})

    def test_as_map_only_filled_providers(self):
        s = PlatformTrackingSettings.load()
        s.is_enabled = True
        s.meta_pixel_id = "123456789"
        s.ga4_measurement_id = ""
        s.tiktok_pixel_id = "CABC123DEF456"
        s.save()
        self.assertEqual(set(s.as_map()), {"meta", "tiktok"})

    def test_platform_tracking_cache_busts_on_save(self):
        self.assertEqual(platform_tracking(), {})
        s = PlatformTrackingSettings.load()
        s.is_enabled = True
        s.meta_pixel_id = "999888777"
        s.save()
        self.assertIn("meta", platform_tracking())

    def test_context_processor_marketing_vs_admin(self):
        s = PlatformTrackingSettings.load()
        s.is_enabled = True
        s.ga4_measurement_id = "G-PLAT123"
        s.save()

        market = RequestFactory().get("/")
        out = platform_pixels(market)
        self.assertIn("gtag/js?id=G-PLAT123", str(out["platform_tracking_head"]))

        admin = RequestFactory().get("/admin/dashboard/")
        self.assertEqual(platform_pixels(admin), {})

    def test_landing_page_carries_pixel_when_enabled(self):
        s = PlatformTrackingSettings.load()
        s.is_enabled = True
        s.meta_pixel_id = "112233445566"
        s.save()
        body = self.client.get("/").content.decode()
        self.assertIn("fbq('init','112233445566')", body)

    def test_landing_page_clean_when_disabled(self):
        body = self.client.get("/").content.decode()
        self.assertNotIn("fbevents.js", body)


@override_settings(ALLOWED_HOSTS=["*"])
class PlatformTrackingScreenTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")
        self.plain = User.objects.create_user("p", "p@t.test", "pw", is_staff=True)

    def test_superadmin_can_open_and_save(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/admin/platform-tracking/").status_code, 200)
        resp = self.client.post("/admin/platform-tracking/", {
            "is_enabled": "on",
            "meta_pixel_id": "123456789012",
            "ga4_measurement_id": "G-ABC1234",
            "tiktok_pixel_id": "",
        })
        self.assertEqual(resp.status_code, 302)
        s = PlatformTrackingSettings.load()
        self.assertTrue(s.is_enabled)
        self.assertEqual(s.meta_pixel_id, "123456789012")

    def test_bad_ga4_id_rejected(self):
        self.client.force_login(self.admin)
        resp = self.client.post("/admin/platform-tracking/", {
            "is_enabled": "on",
            "meta_pixel_id": "",
            "ga4_measurement_id": "nope",
            "tiktok_pixel_id": "",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(PlatformTrackingSettings.load().is_enabled)

    def test_non_admin_denied(self):
        self.client.force_login(self.plain)
        self.assertIn(self.client.get("/admin/platform-tracking/").status_code, (302, 403))
