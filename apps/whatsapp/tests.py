"""Per-store WhatsApp transactional messaging."""

import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.customers.models import Customer
from apps.notifications.models import Channel, Event, NotificationTemplate
from apps.projects.models import Project
from apps.whatsapp import client
from apps.whatsapp.models import (
    WAStatus,
    WhatsAppAccount,
    WhatsAppInbound,
    WhatsAppMessage,
    account_by_phone_number_id,
    account_for,
)
from apps.whatsapp.services import send_transactional

User = get_user_model()


def _account(project, **kw):
    defaults = dict(phone_number_id="PN123", waba_id="WABA1", access_token="tok",
                    is_active=True)
    defaults.update(kw)
    return WhatsAppAccount.objects.create(project=project, **defaults)


def _wa_template(project, event=Event.ORDER_CONFIRMATION, **kw):
    defaults = dict(channel=Channel.WHATSAPP, wa_template_name="order_confirm",
                    wa_language="en_US", body="{name}\n{order_number}\n{total}",
                    is_active=True)
    defaults.update(kw)
    return NotificationTemplate.objects.create(project=project, event=event, **defaults)


class _FakeResp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._p


class ClientTests(TestCase):
    def test_normalize_msisdn(self):
        self.assertEqual(client.normalize_msisdn("+91 98765-43210"), "919876543210")
        self.assertEqual(client.normalize_msisdn(""), "")

    def test_send_template_payload_and_wamid(self):
        acc = WhatsAppAccount(phone_number_id="PN1", access_token="T", graph_version="v21.0")
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode())
            captured["auth"] = req.headers.get("Authorization")
            return _FakeResp({"messages": [{"id": "wamid.ABC"}]})

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            wamid = client.send_template(acc, to="+91 999", name="hi", language="en",
                                         body_params=["A", "B"])
        self.assertEqual(wamid, "wamid.ABC")
        self.assertIn("/PN1/messages", captured["url"])
        self.assertEqual(captured["auth"], "Bearer T")
        body = captured["body"]
        self.assertEqual(body["to"], "91999")
        self.assertEqual(body["template"]["name"], "hi")
        self.assertEqual(
            body["template"]["components"][0]["parameters"],
            [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}],
        )

    def test_http_error_classification(self):
        acc = WhatsAppAccount(phone_number_id="PN1", access_token="T")
        import io
        import urllib.error

        def boom(status, payload):
            def _u(req, timeout=0):
                raise urllib.error.HTTPError(
                    req.full_url, status, "e", {},
                    fp=io.BytesIO(json.dumps(payload).encode()),
                )
            return _u

        with mock.patch("urllib.request.urlopen", boom(400, {"error": {"message": "bad", "code": 100}})):
            with self.assertRaises(client.WhatsAppError) as ctx:
                client.send_template(acc, to="1", name="x")
        self.assertFalse(ctx.exception.retryable)

        with mock.patch("urllib.request.urlopen", boom(500, {"error": {"message": "oops"}})):
            with self.assertRaises(client.WhatsAppError) as ctx:
                client.send_template(acc, to="1", name="x")
        self.assertTrue(ctx.exception.retryable)


class ServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(name="Shop", status="active", currency="INR")

    def test_skipped_without_account(self):
        msg = send_transactional(project=self.project, event=Event.ORDER_CONFIRMATION,
                                 to_number="919", context={})
        self.assertEqual(msg.status, WAStatus.SKIPPED)

    def test_skipped_without_phone(self):
        _account(self.project)
        _wa_template(self.project)
        msg = send_transactional(project=self.project, event=Event.ORDER_CONFIRMATION,
                                 to_number="", context={})
        self.assertEqual(msg.status, WAStatus.SKIPPED)

    def test_skipped_without_template(self):
        _account(self.project)
        msg = send_transactional(project=self.project, event=Event.ORDER_CONFIRMATION,
                                 to_number="919", context={})
        self.assertEqual(msg.status, WAStatus.SKIPPED)
        self.assertIn("template", msg.error.lower())

    def test_accepted_and_params_rendered(self):
        _account(self.project)
        _wa_template(self.project)
        with mock.patch("apps.whatsapp.services.send_template", return_value="wamid.9") as sent:
            msg = send_transactional(
                project=self.project, event=Event.ORDER_CONFIRMATION, to_number="919",
                context={"name": "Ann", "order_number": "A1", "total": "500"},
            )
        self.assertEqual(msg.status, WAStatus.ACCEPTED)
        self.assertEqual(msg.wamid, "wamid.9")
        _, kw = sent.call_args
        self.assertEqual(kw["body_params"], ["Ann", "A1", "500"])
        self.assertEqual(kw["name"], "order_confirm")

    def test_non_retryable_error_logged_not_raised(self):
        _account(self.project)
        _wa_template(self.project)
        err = client.WhatsAppError("nope", retryable=False)
        with mock.patch("apps.whatsapp.services.send_template", side_effect=err):
            msg = send_transactional(project=self.project, event=Event.ORDER_CONFIRMATION,
                                     to_number="919", context={})
        self.assertEqual(msg.status, WAStatus.FAILED)

    def test_retryable_error_raised(self):
        _account(self.project)
        _wa_template(self.project)
        err = client.WhatsAppError("rate", retryable=True)
        with mock.patch("apps.whatsapp.services.send_template", side_effect=err):
            with self.assertRaises(client.WhatsAppError):
                send_transactional(project=self.project, event=Event.ORDER_CONFIRMATION,
                                   to_number="919", context={})


class ModelLookupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(name="Shop", status="active")

    def test_account_for_cache(self):
        self.assertIsNone(account_for(self.project))
        _account(self.project)
        # cached "None" until bust-on-save already happened in create()
        self.assertIsNotNone(account_for(self.project))

    def test_account_by_phone_number_id(self):
        acc = _account(self.project, phone_number_id="PNX")
        self.assertEqual(account_by_phone_number_id("PNX"), acc)
        self.assertIsNone(account_by_phone_number_id("nope"))

    def test_inactive_account_cannot_be_routed_to(self):
        _account(self.project, phone_number_id="PNY", is_active=False)
        self.assertIsNone(account_by_phone_number_id("PNY"))

    def test_two_stores_cannot_share_a_phone_number_id(self):
        from django.db import IntegrityError, transaction

        other = Project.objects.create(name="Other Shop", status="active")
        _account(self.project, phone_number_id="SHARED")
        with self.assertRaises(IntegrityError), transaction.atomic():
            _account(other, phone_number_id="SHARED")

    def test_multiple_stores_can_all_be_unconfigured(self):
        other = Project.objects.create(name="Other Shop 2", status="active")
        _account(self.project, phone_number_id="")
        _account(other, phone_number_id="")  # no IntegrityError


@override_settings(ALLOWED_HOSTS=["*"])
class WebhookTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(name="Shop", status="active")
        self.account = _account(self.project, phone_number_id="PN777")

    def test_get_verify_ok(self):
        r = self.client.get("/whatsapp/webhook/", {
            "hub.mode": "subscribe",
            "hub.verify_token": self.account.verify_token,
            "hub.challenge": "12345",
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"12345")

    def test_get_verify_bad_token(self):
        r = self.client.get("/whatsapp/webhook/", {
            "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "x",
        })
        self.assertEqual(r.status_code, 403)

    def _post(self, payload):
        return self.client.post("/whatsapp/webhook/", data=json.dumps(payload),
                                content_type="application/json")

    def _status_payload(self, wamid, status):
        return {"entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "PN777"},
            "statuses": [{"id": wamid, "status": status, "recipient_id": "91999"}],
        }}]}]}

    def test_status_moves_forward_not_back(self):
        m = WhatsAppMessage.objects.create(project=self.project, to_number="91999",
                                           wamid="wamid.1", status=WAStatus.ACCEPTED)
        self._post(self._status_payload("wamid.1", "delivered"))
        m.refresh_from_db()
        self.assertEqual(m.status, WAStatus.DELIVERED)
        self._post(self._status_payload("wamid.1", "sent"))  # older signal
        m.refresh_from_db()
        self.assertEqual(m.status, WAStatus.DELIVERED)

    def test_failed_status_records_error(self):
        m = WhatsAppMessage.objects.create(project=self.project, to_number="91999",
                                           wamid="wamid.2", status=WAStatus.DELIVERED)
        payload = self._status_payload("wamid.2", "failed")
        payload["entry"][0]["changes"][0]["value"]["statuses"][0]["errors"] = [
            {"title": "Message undeliverable"}
        ]
        self._post(payload)
        m.refresh_from_db()
        self.assertEqual(m.status, WAStatus.FAILED)
        self.assertEqual(m.error, "Message undeliverable")

    def test_inbound_stop_opts_customer_out(self):
        Customer.objects.create(project=self.project, email="c@t.test",
                                phone="9876543210", marketing_opt_in=True)
        payload = {"entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "PN777"},
            "messages": [{"from": "919876543210", "id": "wamid.in", "type": "text",
                          "text": {"body": "STOP"}}],
        }}]}]}
        self._post(payload)
        self.assertTrue(WhatsAppInbound.objects.filter(is_opt_out=True).exists())
        self.assertFalse(Customer.objects.get(email="c@t.test").marketing_opt_in)

    def test_unknown_phone_number_id_ignored(self):
        r = self._post(self._status_payload("wamid.x", "sent"))
        self.assertEqual(r.status_code, 200)


@override_settings(ALLOWED_HOSTS=["*"])
class ConnectFormDuplicatePhoneNumberTests(TestCase):
    def setUp(self):
        cache.clear()
        self.taken = Project.objects.create(name="Taken Co", status="active")
        _account(self.taken, phone_number_id="PN999")
        self.mine = Project.objects.create(name="Mine Co", status="active",
                                           feature_flags={"onboarded": True})
        self.owner = User.objects.create_user("wo", "wo@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.mine, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.mine.pk
        s.save()

    def test_reusing_another_stores_phone_number_id_is_a_form_error_not_500(self):
        resp = self.client.post("/admin/settings/whatsapp/", {
            "phone_number_id": "PN999", "waba_id": "W1",
            "access_token": "tok", "graph_version": "v21.0",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already connected to another store")
        self.assertFalse(WhatsAppAccount.objects.filter(project=self.mine, phone_number_id="PN999").exists())


class NotificationWiringTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(name="Shop", status="active", currency="INR")

    def test_signal_enqueues_whatsapp_when_active_and_phone(self):
        _account(self.project)
        from apps.core.events import Events, emit
        with mock.patch("apps.whatsapp.tasks.send_whatsapp_task.delay") as delay, \
             mock.patch("apps.notifications.tasks.send_notification_task.delay"):
            emit(Events.ORDER_CREATED, project=self.project,
                 payload={"order_number": "A1", "email": "c@t.test", "phone": "919999",
                          "name": "Ann", "total": "500", "currency": "INR"},
                 instance=None)
        delay.assert_called_once()
        args = delay.call_args.args
        self.assertEqual(args[0], self.project.id)
        self.assertEqual(args[2], "919999")

    def test_signal_no_whatsapp_without_account(self):
        from apps.core.events import Events, emit
        with mock.patch("apps.whatsapp.tasks.send_whatsapp_task.delay") as delay, \
             mock.patch("apps.notifications.tasks.send_notification_task.delay"):
            emit(Events.ORDER_CREATED, project=self.project,
                 payload={"order_number": "A1", "phone": "919999"}, instance=None)
        delay.assert_not_called()

    def test_signal_no_whatsapp_without_phone(self):
        _account(self.project)
        from apps.core.events import Events, emit
        with mock.patch("apps.whatsapp.tasks.send_whatsapp_task.delay") as delay, \
             mock.patch("apps.notifications.tasks.send_notification_task.delay"):
            emit(Events.ORDER_CREATED, project=self.project,
                 payload={"order_number": "A1", "email": "c@t.test"}, instance=None)
        delay.assert_not_called()


@override_settings(ALLOWED_HOSTS=["*"])
class AdminScreenTests(TestCase):
    def setUp(self):
        cache.clear()
        self.store = Project.objects.create(name="ShopCo", status="active",
                                            feature_flags={"onboarded": True})
        self.owner = User.objects.create_user("o", "o@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.owner, role=StoreRole.OWNER)
        self.staff = User.objects.create_user("s", "s@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.staff, role=StoreRole.STAFF)

    def _login(self, user):
        self.client.force_login(user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.store.pk
        s.save()

    def test_owner_opens_staff_denied(self):
        self._login(self.owner)
        self.assertEqual(self.client.get("/admin/settings/whatsapp/").status_code, 200)
        self._login(self.staff)
        self.assertEqual(self.client.get("/admin/settings/whatsapp/").status_code, 403)

    def test_save_verifies_and_activates(self):
        self._login(self.owner)
        info = {"display_phone": "+91 99999 00000", "verified_name": "ShopCo", "quality_rating": "GREEN"}
        with mock.patch("apps.whatsapp.client.fetch_number", return_value=info):
            resp = self.client.post("/admin/settings/whatsapp/", {
                "phone_number_id": "PN42", "waba_id": "WABA9",
                "access_token": "permatoken", "app_secret": "", "graph_version": "v21.0",
            })
        self.assertEqual(resp.status_code, 302)
        acc = WhatsAppAccount.objects.get(project=self.store)
        self.assertTrue(acc.is_active)
        self.assertEqual(acc.display_phone, "+91 99999 00000")

    def test_save_bad_credentials_not_activated(self):
        self._login(self.owner)
        with mock.patch("apps.whatsapp.client.fetch_number",
                        side_effect=client.WhatsAppError("invalid token")):
            resp = self.client.post("/admin/settings/whatsapp/", {
                "phone_number_id": "PN42", "waba_id": "", "access_token": "bad",
                "app_secret": "", "graph_version": "v21.0",
            })
        self.assertEqual(resp.status_code, 302)
        acc = WhatsAppAccount.objects.get(project=self.store)
        self.assertFalse(acc.is_active)
        self.assertIn("invalid token", acc.last_error)
