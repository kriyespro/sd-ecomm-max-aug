"""Trial-ending-soon email reminder — Subscription.trial_end + auto-suspend
already existed, but nothing warned the owner *before* it happened unless
they happened to log into Mission Control in the last 3 days (the banner
in apps.control.context_processors). This is the out-of-band nudge."""

from datetime import timedelta

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Membership, StoreRole
from apps.billing import services as billing_svc
from apps.billing.models import SubscriptionStatus
from apps.notifications.models import Event, NotificationLog
from apps.projects.models import Project
from django.contrib.auth import get_user_model

User = get_user_model()


class TrialEndingSoonQueryTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="RemindCo", status="active")
        self.sub = billing_svc.ensure_subscription(self.project)

    def test_included_when_inside_window(self):
        self.sub.trial_end = timezone.now() + timedelta(days=2)
        self.sub.save(update_fields=["trial_end"])
        self.assertIn(self.sub, billing_svc.trial_ending_soon())

    def test_excluded_when_far_out(self):
        self.sub.trial_end = timezone.now() + timedelta(days=10)
        self.sub.save(update_fields=["trial_end"])
        self.assertNotIn(self.sub, billing_svc.trial_ending_soon())

    def test_excluded_once_already_reminded(self):
        self.sub.trial_end = timezone.now() + timedelta(days=2)
        self.sub.trial_reminder_sent_at = timezone.now()
        self.sub.save(update_fields=["trial_end", "trial_reminder_sent_at"])
        self.assertNotIn(self.sub, billing_svc.trial_ending_soon())

    def test_excluded_when_comp(self):
        self.sub.trial_end = timezone.now() + timedelta(days=2)
        self.sub.is_comp = True
        self.sub.save(update_fields=["trial_end", "is_comp"])
        self.assertNotIn(self.sub, billing_svc.trial_ending_soon())

    def test_excluded_when_dgc_managed(self):
        dgc = User.objects.create_user("dgc1", "dgc1@t.test", "pw")
        self.sub.trial_end = timezone.now() + timedelta(days=2)
        self.sub.manager = dgc
        self.sub.save(update_fields=["trial_end", "manager"])
        self.assertNotIn(self.sub, billing_svc.trial_ending_soon())

    def test_excluded_once_trial_over(self):
        self.sub.trial_end = timezone.now() - timedelta(hours=1)
        self.sub.save(update_fields=["trial_end"])
        self.assertNotIn(self.sub, billing_svc.trial_ending_soon())

    def test_excluded_when_not_trialing(self):
        self.sub.trial_end = timezone.now() + timedelta(days=2)
        self.sub.status = SubscriptionStatus.ACTIVE
        self.sub.save(update_fields=["trial_end", "status"])
        self.assertNotIn(self.sub, billing_svc.trial_ending_soon())


class SendTrialEndingRemindersTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="RemindCo2", status="active")
        self.sub = billing_svc.ensure_subscription(self.project)
        self.sub.trial_end = timezone.now() + timedelta(days=2, hours=1)
        self.sub.save(update_fields=["trial_end"])
        self.owner = User.objects.create_user("owner", "owner@t.test", "pw")
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)

    def test_sends_once_and_marks_reminded(self):
        sent = billing_svc.send_trial_ending_reminders()
        self.assertEqual(len(sent), 1)
        self.sub.refresh_from_db()
        self.assertIsNotNone(self.sub.trial_reminder_sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("2 day", mail.outbox[0].subject)

        # second run: already reminded, no repeat send
        mail.outbox.clear()
        sent_again = billing_svc.send_trial_ending_reminders()
        self.assertEqual(len(sent_again), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_logs_against_the_owner_email(self):
        billing_svc.send_trial_ending_reminders()
        log = NotificationLog.objects.filter(project=self.project, event=Event.TRIAL_ENDING).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.to_address, "owner@t.test")

    def test_skips_store_with_no_resolvable_owner_email(self):
        Membership.objects.filter(project=self.project).delete()
        self.owner.email = ""
        self.owner.save(update_fields=["email"])
        sent = billing_svc.send_trial_ending_reminders()
        self.assertEqual(sent, [])
        self.sub.refresh_from_db()
        self.assertIsNone(self.sub.trial_reminder_sent_at)
