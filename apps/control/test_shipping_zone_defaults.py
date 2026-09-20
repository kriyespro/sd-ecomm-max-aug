"""A brand-new shipping zone form pre-fills a "Surat, Gujarat, India" self-ship
zone -- most stores ship locally first, so this cuts blank-form friction on
/admin/shipping/zones/new/. Editing an existing zone must NOT be touched."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project
from apps.shipping.models import ShippingZone

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ZoneCreateDefaultsTests(TestCase):
    def setUp(self):
        self.store = Project.objects.create(
            name="ShipCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("so", "so@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.store.pk
        s.save()

    def test_new_zone_form_prefilled_with_surat(self):
        resp = self.client.get("/admin/shipping/zones/new/")
        body = resp.content.decode()
        self.assertIn("Surat, Gujarat", body)
        self.assertIn("IN", body)
        self.assertIn("Gujarat", body)
        self.assertIn("394", body)
        self.assertIn("395", body)

    def test_defaults_are_editable_and_save_as_submitted(self):
        resp = self.client.post("/admin/shipping/zones/new/", {
            "name": "Mumbai", "is_active": "on", "priority": "100",
            "countries": '["IN"]', "states": '["Maharashtra"]', "postal_prefixes": '["400"]',
        })
        self.assertEqual(resp.status_code, 302)
        zone = ShippingZone.objects.get(project=self.store)
        self.assertEqual(zone.name, "Mumbai")
        self.assertEqual(zone.states, ["Maharashtra"])

    def test_editing_an_existing_zone_is_not_reset_to_surat(self):
        zone = ShippingZone.objects.create(
            project=self.store, name="Delhi", countries=["IN"], states=["Delhi"],
            postal_prefixes=["110"],
        )
        resp = self.client.get(f"/admin/shipping/zones/{zone.pk}/")
        body = resp.content.decode()
        self.assertIn("Delhi", body)
        self.assertNotIn("Surat", body)
