"""Segment -> WhatsApp broadcast task: only opted-in customers with a phone
in the target segment get queued; everything else is skipped cleanly."""

from unittest.mock import patch

from django.test import TestCase

from apps.customers.models import Customer, Segment
from apps.customers.tasks import send_segment_broadcast_task
from apps.projects.models import Project
from apps.whatsapp.models import WhatsAppAccount


class SegmentBroadcastTaskTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="BroadcastCo", status="active", currency="INR")

    def _account(self):
        return WhatsAppAccount.objects.create(
            project=self.project, phone_number_id="123", access_token="tok", is_active=True,
        )

    def test_no_account_sends_nothing(self):
        Customer.objects.create(
            project=self.project, email="a@t.test", phone="+911111111111",
            marketing_opt_in=True, segment=Segment.VIP,
        )
        sent = send_segment_broadcast_task(self.project.pk, Segment.VIP, "hello")
        self.assertEqual(sent, 0)

    @patch("apps.whatsapp.tasks.send_whatsapp_task.delay")
    def test_only_opted_in_customers_with_phone_in_segment_are_queued(self, mock_delay):
        self._account()
        Customer.objects.create(
            project=self.project, email="vip1@t.test", phone="+911111111111",
            marketing_opt_in=True, segment=Segment.VIP,
        )
        Customer.objects.create(
            project=self.project, email="vip_no_optin@t.test", phone="+912222222222",
            marketing_opt_in=False, segment=Segment.VIP,
        )
        Customer.objects.create(
            project=self.project, email="vip_no_phone@t.test", phone="",
            marketing_opt_in=True, segment=Segment.VIP,
        )
        Customer.objects.create(
            project=self.project, email="other@t.test", phone="+913333333333",
            marketing_opt_in=True, segment=Segment.NEW,
        )
        sent = send_segment_broadcast_task(self.project.pk, Segment.VIP, "Sale!")
        self.assertEqual(sent, 1)
        self.assertEqual(mock_delay.call_count, 1)

    @patch("apps.whatsapp.tasks.send_whatsapp_task.delay", side_effect=Exception("boom"))
    def test_one_bad_customer_does_not_sink_the_batch(self, mock_delay):
        self._account()
        Customer.objects.create(
            project=self.project, email="vip1@t.test", phone="+911111111111",
            marketing_opt_in=True, segment=Segment.VIP,
        )
        Customer.objects.create(
            project=self.project, email="vip2@t.test", phone="+912222222222",
            marketing_opt_in=True, segment=Segment.VIP,
        )
        sent = send_segment_broadcast_task(self.project.pk, Segment.VIP, "Sale!")
        self.assertEqual(sent, 0)  # both raised, but the loop completed both iterations
        self.assertEqual(mock_delay.call_count, 2)
