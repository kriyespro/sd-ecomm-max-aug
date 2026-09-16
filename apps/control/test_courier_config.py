"""Mission Control screen for courier accounts — the place the site owner
adds/updates their Shiprocket or Delhivery keys, mirroring the payment
provider screen exactly."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project
from apps.shipping.models import Courier, CourierConfig

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class CourierConfigScreenTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="CourierAdminCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("o2", "o2@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_list_empty_state(self):
        resp = self.client.get("/admin/shipping/couriers/")
        self.assertContains(resp, "No courier accounts yet")

    def test_create_shiprocket_saves_credentials(self):
        resp = self.client.post("/admin/shipping/couriers/new/", {
            "courier": "shiprocket", "is_enabled": "on", "is_test_mode": "on",
            "email": "owner@shop.test", "password": "s3cret", "api_token": "",
            "pickup_location": "Warehouse A",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        cfg = CourierConfig.objects.get(project=self.project, courier=Courier.SHIPROCKET)
        self.assertEqual(cfg.credentials["email"], "owner@shop.test")
        self.assertEqual(cfg.credentials["password"], "s3cret")
        self.assertEqual(cfg.credentials["pickup_location"], "Warehouse A")

    def test_enabling_without_credentials_is_rejected(self):
        resp = self.client.post("/admin/shipping/couriers/new/", {
            "courier": "shiprocket", "is_enabled": "on", "is_test_mode": "on",
            "email": "", "password": "", "api_token": "", "pickup_location": "",
        })
        self.assertEqual(resp.status_code, 200)  # form re-rendered with errors
        self.assertFalse(CourierConfig.objects.filter(project=self.project).exists())

    def test_edit_blank_password_keeps_existing_secret(self):
        cfg = CourierConfig.objects.create(
            project=self.project, courier=Courier.SHIPROCKET, is_enabled=True,
            credentials={"email": "owner@shop.test", "password": "original-pw"},
        )
        resp = self.client.post(f"/admin/shipping/couriers/{cfg.pk}/", {
            "courier": "shiprocket", "is_enabled": "on", "is_test_mode": "on",
            "email": "owner@shop.test", "password": "", "api_token": "",
            "pickup_location": "",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        cfg.refresh_from_db()
        self.assertEqual(cfg.credentials["password"], "original-pw")

    def test_password_never_echoed_back_in_edit_form(self):
        cfg = CourierConfig.objects.create(
            project=self.project, courier=Courier.SHIPROCKET,
            credentials={"email": "owner@shop.test", "password": "original-pw"},
        )
        resp = self.client.get(f"/admin/shipping/couriers/{cfg.pk}/")
        self.assertNotContains(resp, "original-pw")

    def test_staff_role_cannot_manage_courier_accounts(self):
        staff = User.objects.create_user("staffer", "s@t.test", "pw")
        Membership.objects.create(project=self.project, user=staff, role=StoreRole.STAFF)
        self.client.force_login(staff)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        resp = self.client.get("/admin/shipping/couriers/")
        self.assertEqual(resp.status_code, 403)
