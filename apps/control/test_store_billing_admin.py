"""Super-admin store billing: plan preselect, trial auto-suspend, gift, mark-paid."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing import services as billing_svc
from apps.billing.models import (
    BillingPeriod,
    BillingSettings,
    InvoiceStatus,
    Plan,
    SubscriptionStatus,
)
from apps.projects.models import Project

User = get_user_model()


class TrialAutoSuspendTests(TestCase):
    def test_expired_trial_gets_invoiced_then_suspended(self):
        p = Project.objects.create(name="Triallo")
        sub = p.subscription  # trial, created by the post_save signal
        self.assertEqual(sub.status, SubscriptionStatus.TRIALING)

        # Fast-forward past the trial.
        sub.current_period_end = timezone.now() - timedelta(days=1)
        sub.trial_end = sub.current_period_end
        sub.save(update_fields=["current_period_end", "trial_end"])

        billing_svc.issue_due_invoices()
        inv = sub.invoices.get()
        self.assertEqual(inv.status, InvoiceStatus.OPEN)

        # Still inside the grace window -> not suspended yet.
        billing_svc.suspend_overdue()
        sub.refresh_from_db()
        self.assertNotEqual(sub.status, SubscriptionStatus.SUSPENDED)

        # Past the invoice due date -> suspended, storefront goes offline.
        inv.due_at = timezone.now() - timedelta(hours=1)
        inv.save(update_fields=["due_at"])
        billing_svc.suspend_overdue()
        sub.refresh_from_db()
        self.assertEqual(sub.status, SubscriptionStatus.SUSPENDED)


class AdminAdjustTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Gifty")
        self.sub = self.project.subscription
        self.growth = Plan.objects.get(code="growth")

    def test_gift_makes_store_free_and_never_suspends(self):
        billing_svc.admin_adjust(self.sub, plan=self.growth,
                                 period=BillingPeriod.YEARLY, comp=True)
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.is_comp)
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertEqual(self.sub.current_price(), 0)

        # A comp store is never picked up for invoicing or suspension.
        self.sub.current_period_end = timezone.now() - timedelta(days=1)
        self.sub.save(update_fields=["current_period_end"])
        self.assertEqual(billing_svc.issue_due_invoices(), [])
        self.assertEqual(self.sub.invoices.count(), 0)

    def test_ungift_returns_to_normal_billing(self):
        billing_svc.admin_adjust(self.sub, comp=True)
        billing_svc.admin_adjust(self.sub, comp=False)
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.is_comp)
        self.assertIsNone(self.sub.override_price)

    def test_mark_paid_settles_open_invoice(self):
        self.sub.status = SubscriptionStatus.ACTIVE
        self.sub.current_period_end = timezone.now() - timedelta(days=1)
        self.sub.save(update_fields=["status", "current_period_end"])
        billing_svc.issue_invoice(self.sub)

        billing_svc.admin_mark_paid(self.sub)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatus.ACTIVE)
        self.assertEqual(self.sub.invoices.get().status, InvoiceStatus.PAID)


@override_settings(PLATFORM_HOSTS=["shopinaday.com"], PLATFORM_BASE_DOMAIN="shopinaday.com")
class StoreCreateDefaultsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")
        self.client.force_login(self.admin)

    def test_form_preselects_growth_and_yearly(self):
        resp = self.client.get("/admin/stores/new/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        growth = Plan.objects.get(code="growth")
        self.assertIn(f'value="{growth.pk}" selected', body)
        self.assertIn('value="yearly" selected', body)

    def test_owner_password_saves_without_error(self):
        resp = self.client.post("/admin/stores/new/", {
            "name": "Pw Store", "subdomain": "", "primary_domain": "",
            "currency": "INR", "country": "IN",
            "owner_email": "o@pw.test", "owner_name": "O",
            "owner_password": "Zx9!kLmq7Ww",
            "plan": Plan.objects.get(code="growth").pk, "period": "yearly",
        })
        self.assertEqual(resp.status_code, 302)
        owner = User.objects.get(email="o@pw.test")
        self.assertTrue(owner.check_password("Zx9!kLmq7Ww"))
        sub = Project.objects.get(name="Pw Store").subscription
        self.assertEqual(sub.plan.code, "growth")
        self.assertEqual(sub.period, BillingPeriod.YEARLY)

    def test_admin_can_gift_from_store_screen(self):
        p = Project.objects.create(name="Screeny")
        resp = self.client.post(f"/admin/stores/{p.pk}/billing/", {
            "plan": Plan.objects.get(code="growth").pk,
            "period": "yearly", "comp": "on",
        })
        self.assertEqual(resp.status_code, 302)
        p.subscription.refresh_from_db()
        self.assertTrue(p.subscription.is_comp)
