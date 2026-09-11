"""Meta OAuth ("Connect with Meta") state must be bound to the store that
started the flow — otherwise a store switch mid-flow (another tab, a reused
session) could attach the fetched pixel/token to the wrong store."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.marketing.models import TrackingIntegration
from apps.projects.models import Project

User = get_user_model()

_META_SETTINGS = dict(META_APP_ID="app123", META_APP_SECRET="secret123")


@override_settings(**_META_SETTINGS)
class MetaOAuthStateBindingTests(TestCase):
    def setUp(self):
        self.store_a = Project.objects.create(name="Store A", status="active", feature_flags={"onboarded": True})
        self.store_b = Project.objects.create(name="Store B", status="active", feature_flags={"onboarded": True})
        self.owner = User.objects.create_user("mo", "mo@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store_a, user=self.owner, role=StoreRole.OWNER, is_active=True)
        Membership.objects.create(project=self.store_b, user=self.owner, role=StoreRole.OWNER, is_active=True)
        self.client.force_login(self.owner)

    def _set_active(self, project):
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = project.pk
        s.save()

    def test_switching_active_store_mid_flow_is_rejected(self):
        self._set_active(self.store_a)
        start = self.client.get("/admin/marketing/tracking/meta/connect/")
        self.assertEqual(start.status_code, 302)
        state = self.client.session["meta_oauth_state"]["state"]

        # Store switches to B in "another tab" before the callback returns.
        self._set_active(self.store_b)
        with mock.patch("apps.control.marketing_views.meta_oauth.exchange_code") as exch:
            resp = self.client.get(
                "/admin/marketing/tracking/meta/callback/",
                {"state": state, "code": "abc"}, follow=True,
            )
        self.assertContains(resp, "Meta connection expired")
        exch.assert_not_called()
        self.assertFalse(TrackingIntegration.objects.filter(project=self.store_b).exists())

    def test_same_store_completes_the_connection(self):
        self._set_active(self.store_a)
        self.client.get("/admin/marketing/tracking/meta/connect/")
        state = self.client.session["meta_oauth_state"]["state"]

        with mock.patch("apps.control.marketing_views.meta_oauth.exchange_code", return_value="tok"), \
             mock.patch("apps.control.marketing_views.meta_oauth.list_pixels",
                        return_value=[{"id": "px1", "name": "My Pixel"}]):
            resp = self.client.get(
                "/admin/marketing/tracking/meta/callback/",
                {"state": state, "code": "abc"}, follow=True,
            )
        self.assertContains(resp, "Connected Meta Pixel")
        self.assertTrue(TrackingIntegration.objects.filter(project=self.store_a, pixel_id="px1").exists())
