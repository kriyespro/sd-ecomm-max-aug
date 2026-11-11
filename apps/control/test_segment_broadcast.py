"""Marketing -> Segments (/admin/marketing/segments/): segment counts +
the broadcast-send action. Task-level behavior covered by
apps.customers.test_segment_broadcast."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.customers.models import Customer, Segment
from apps.notifications.models import Channel, Event, NotificationTemplate
from apps.projects.models import Project
from apps.whatsapp.models import WhatsAppAccount

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class SegmentBroadcastAdminTests(TestCase):
    def setUp(self):
        # account_for(project) caches on project.pk for 600s; SQLite test
        # rollback can reuse the same pk across tests in this class.
        cache.clear()
        self.project = Project.objects.create(
            name="SegAdminCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("sao", "sao@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        Customer.objects.create(
            project=self.project, email="vip@t.test", phone="+911111111111",
            marketing_opt_in=True, segment=Segment.VIP,
        )

    def test_screen_reachable_and_shows_counts(self):
        resp = self.client.get("/admin/marketing/segments/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "VIP")

    def test_nav_lists_the_screen(self):
        body = self.client.get("/admin/products/").content.decode()
        self.assertIn("/admin/marketing/segments/", body)

    def test_send_without_whatsapp_account_errors(self):
        resp = self.client.post("/admin/marketing/segments/send/", {
            "segment": "vip", "message": "hi",
        })
        self.assertRedirects(resp, "/admin/marketing/segments/", fetch_redirect_response=False)
        follow = self.client.get("/admin/marketing/segments/")
        self.assertContains(follow, "Connect WhatsApp")

    def test_send_without_template_errors(self):
        WhatsAppAccount.objects.create(
            project=self.project, phone_number_id="1", access_token="t", is_active=True,
        )
        resp = self.client.post("/admin/marketing/segments/send/", {
            "segment": "vip", "message": "hi",
        })
        self.assertRedirects(resp, "/admin/marketing/segments/", fetch_redirect_response=False)
        follow = self.client.get("/admin/marketing/segments/")
        self.assertContains(follow, "WhatsApp template")

    @patch("apps.customers.tasks.send_segment_broadcast_task.delay")
    def test_send_with_everything_configured_queues_the_task(self, mock_delay):
        WhatsAppAccount.objects.create(
            project=self.project, phone_number_id="1", access_token="t", is_active=True,
        )
        NotificationTemplate.objects.create(
            project=self.project, event=Event.MARKETING_BROADCAST, channel=Channel.WHATSAPP,
            wa_template_name="marketing_broadcast_v1",
        )
        resp = self.client.post("/admin/marketing/segments/send/", {
            "segment": "vip", "message": "Flat 20% off",
        })
        self.assertRedirects(resp, "/admin/marketing/segments/", fetch_redirect_response=False)
        mock_delay.assert_called_once_with(self.project.pk, "vip", "Flat 20% off")
        follow = self.client.get("/admin/marketing/segments/")
        self.assertContains(follow, "Queued to 1")

    def test_staff_without_manage_role_is_denied(self):
        staff = User.objects.create_user("sas", "sas@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=staff, role=StoreRole.STAFF)
        self.client.force_login(staff)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        resp = self.client.get("/admin/marketing/segments/")
        self.assertEqual(resp.status_code, 403)
