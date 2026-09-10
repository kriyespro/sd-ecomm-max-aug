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
from apps.marketing import capi
from apps.marketing.models import (
    TrackingIntegration,
    TrackingProvider,
    tracking_for,
)
from apps.marketing.tasks import send_meta_capi_event
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
