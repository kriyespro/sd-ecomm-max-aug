"""Social auto-share: OAuth, clients, share logic, product-publish signal."""

import json
import time
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Membership, StoreRole
from apps.catalog.models import Product, ProductImage
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project
from apps.social import clients, oauth
from apps.social.models import (
    ShareStatus,
    SocialAccount,
    SocialPost,
    SocialProvider,
    auto_share_targets,
)
from apps.social.services import ensure_fresh, share_product

User = get_user_model()


def _account(project, provider=SocialProvider.PINTEREST, **kw):
    defaults = dict(
        client_id="cid", client_secret="csec",
        access_token="at", refresh_token="rt",
        token_expires_at=timezone.now() + timezone.timedelta(hours=2),
        target_id="board-1", target_name="Board 1",
        is_active=True, auto_share=True,
    )
    defaults.update(kw)
    return SocialAccount.objects.create(project=project, provider=provider, **defaults)


def _product(project, slug="lamp", with_image=True, **kw):
    kw.setdefault("status", "active")
    p = Product.objects.create(project=project, title="Lamp", slug=slug,
                               price=Decimal("999"), **kw)
    if with_image:
        ProductImage.objects.create(product=p, image="products/lamp.jpg", is_primary=True)
    return p


class _Resp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._p


class OAuthTests(TestCase):
    def test_authorize_url_per_provider(self):
        u = oauth.authorize_url("pinterest", client_id="X", redirect_uri="https://cb", state="s")
        self.assertIn("pinterest.com/oauth", u)
        self.assertIn("pins%3Awrite", u)
        g = oauth.authorize_url("gbp", client_id="X", redirect_uri="https://cb", state="s")
        self.assertIn("accounts.google.com", g)
        self.assertIn("access_type=offline", g)

    def test_exchange_code_pinterest_uses_basic_auth(self):
        captured = {}

        def fake(req, timeout=0):
            captured["auth"] = req.headers.get("Authorization")
            captured["body"] = req.data.decode()
            return _Resp({"access_token": "a", "refresh_token": "r", "expires_in": 3600})

        with mock.patch("urllib.request.urlopen", fake):
            tok = oauth.exchange_code("pinterest", code="c", redirect_uri="https://cb",
                                      client_id="id", client_secret="sec")
        self.assertTrue(captured["auth"].startswith("Basic "))
        self.assertEqual(tok["access_token"], "a")
        self.assertGreater(tok["expires_at"], time.time())

    def test_refresh_keeps_old_refresh_token(self):
        def fake(req, timeout=0):
            return _Resp({"access_token": "a2", "expires_in": 3600})

        with mock.patch("urllib.request.urlopen", fake):
            tok = oauth.refresh("gbp", refresh_token="keepme", client_id="i", client_secret="s")
        self.assertEqual(tok["refresh_token"], "keepme")


class ClientTests(TestCase):
    def test_pinterest_create_pin_payload(self):
        captured = {}

        def fake(req, timeout=0):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode())
            return _Resp({"id": "pin99"})

        with mock.patch("urllib.request.urlopen", fake):
            pid, url = clients.pinterest_create_pin(
                "tok", board_id="b1", title="T", description="D",
                link="https://s/p/x/", image_url="https://s/i.jpg")
        self.assertEqual(pid, "pin99")
        self.assertIn("pin99", url)
        self.assertEqual(captured["body"]["board_id"], "b1")
        self.assertEqual(captured["body"]["media_source"]["url"], "https://s/i.jpg")

    def test_gbp_create_post_payload(self):
        def fake(req, timeout=0):
            body = json.loads(req.data.decode())
            assert body["callToAction"]["actionType"] == "SHOP"
            assert body["media"][0]["sourceUrl"] == "https://s/i.jpg"
            return _Resp({"name": "accounts/1/locations/2/localPosts/9"})

        with mock.patch("urllib.request.urlopen", fake):
            name, _ = clients.gbp_create_post(
                "tok", location="accounts/1/locations/2", summary="hi",
                link="https://s/p/x/", image_url="https://s/i.jpg")
        self.assertIn("localPosts/9", name)

    def test_http_5xx_is_retryable(self):
        import io
        import urllib.error

        def fake(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, 503, "e", {}, io.BytesIO(b"{}"))

        with mock.patch("urllib.request.urlopen", fake):
            with self.assertRaises(clients.SocialAPIError) as ctx:
                clients.pinterest_user("t")
        self.assertTrue(ctx.exception.retryable)


class ShareTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(name="Shop", status="active", currency="INR")
        # Don't let the publish signal fire real (eager) shares while we set up.
        p = mock.patch("apps.social.tasks.share_product_task.delay")
        p.start()
        self.addCleanup(p.stop)

    def test_skipped_without_image(self):
        acc = _account(self.project)
        prod = _product(self.project, with_image=False)
        with mock.patch("apps.social.clients.pinterest_create_pin") as pin:
            post = share_product(acc, prod)
        self.assertEqual(post.status, ShareStatus.SKIPPED)
        pin.assert_not_called()

    def test_posts_and_is_idempotent(self):
        acc = _account(self.project)
        prod = _product(self.project)
        with mock.patch("apps.social.clients.pinterest_create_pin",
                        return_value=("pin1", "https://pin/1")) as pin:
            post = share_product(acc, prod)
            again = share_product(acc, prod)
        self.assertEqual(post.status, ShareStatus.POSTED)
        self.assertEqual(post.external_id, "pin1")
        self.assertEqual(pin.call_count, 1)          # second call is a no-op
        self.assertEqual(again.pk, post.pk)

    def test_failed_non_retryable_logged(self):
        acc = _account(self.project)
        prod = _product(self.project, slug="lamp2")
        err = clients.SocialAPIError("bad board", retryable=False)
        with mock.patch("apps.social.clients.pinterest_create_pin", side_effect=err):
            post = share_product(acc, prod)
        self.assertEqual(post.status, ShareStatus.FAILED)

    def test_retryable_raises(self):
        acc = _account(self.project)
        prod = _product(self.project, slug="lamp3")
        err = clients.SocialAPIError("boom", retryable=True)
        with mock.patch("apps.social.clients.pinterest_create_pin", side_effect=err):
            with self.assertRaises(clients.SocialAPIError):
                share_product(acc, prod)

    def test_ensure_fresh_refreshes_expired(self):
        acc = _account(self.project, token_expires_at=timezone.now() - timezone.timedelta(minutes=1))
        with mock.patch("apps.social.oauth.refresh", return_value={
            "access_token": "new", "refresh_token": "rt2", "scope": "",
            "expires_at": time.time() + 3600,
        }) as r:
            ensure_fresh(acc)
        r.assert_called_once()
        acc.refresh_from_db()
        self.assertEqual(acc.access_token, "new")


class SignalTests(TestCase):
    def setUp(self):
        cache.clear()
        self.project = Project.objects.create(name="Shop", status="active", currency="INR")
        self._delay = mock.patch("apps.social.tasks.share_product_task.delay").start()
        self.addCleanup(mock.patch.stopall)

    def test_publish_enqueues_for_each_ready_target(self):
        _account(self.project, provider=SocialProvider.PINTEREST)
        _account(self.project, provider=SocialProvider.GBP,
                 target_id="accounts/1/locations/2")
        _product(self.project, slug="new-lamp")
        self.assertEqual(self._delay.call_count, 2)

    def test_no_enqueue_when_auto_share_off(self):
        _account(self.project, auto_share=False)
        _product(self.project, slug="l2")
        self._delay.assert_not_called()

    def test_no_reenqueue_when_post_exists(self):
        acc = _account(self.project)
        prod = _product(self.project, slug="l3")
        SocialPost.objects.create(project=self.project, provider=acc.provider,
                                  product=prod, status=ShareStatus.POSTED)
        self._delay.reset_mock()
        prod.title = "changed"
        prod.save()
        self._delay.assert_not_called()

    def test_draft_product_not_shared(self):
        _account(self.project)
        _product(self.project, slug="l4", status="draft")
        self._delay.assert_not_called()

    def test_auto_share_targets_filters(self):
        _account(self.project, is_active=False)
        self.assertEqual(auto_share_targets(self.project), [])


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
        self.assertEqual(self.client.get("/admin/marketing/social/").status_code, 200)
        self._login(self.staff)
        self.assertEqual(self.client.get("/admin/marketing/social/").status_code, 403)

    def test_save_creds_then_connect_redirects_to_provider(self):
        self._login(self.owner)
        self.client.post("/admin/marketing/social/pinterest/creds/",
                         {"client_id": "abc", "client_secret": "xyz"})
        acc = SocialAccount.objects.get(project=self.store, provider="pinterest")
        self.assertEqual(acc.client_id, "abc")
        resp = self.client.get("/admin/marketing/social/pinterest/connect/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("pinterest.com/oauth", resp["Location"])
