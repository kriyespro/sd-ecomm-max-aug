"""Marketing -> Referral program (/admin/marketing/referrals/): settings
form + referrer/commission management table. Storefront-side behavior
(become a referrer, beacon click tracking, checkout attribution) is covered
by apps.shopfront.test_referrals; service-level logic by apps.referrals.tests."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.orders.models import Order
from apps.projects.models import Project
from apps.referrals.models import CommissionStatus, ReferralCommission, ReferralProgram, Referrer, ReferrerStatus

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class ReferralProgramSettingsTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="ReferCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("ro", "ro@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_screen_reachable(self):
        resp = self.client.get("/admin/marketing/referrals/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Referral program")

    def test_saving_turns_the_program_on(self):
        resp = self.client.post("/admin/marketing/referrals/", {
            "is_active": "on", "commission_type": "percent",
            "commission_value": "15", "cookie_days": "45", "minimum_payout": "500",
        })
        self.assertRedirects(resp, "/admin/marketing/referrals/")
        program = ReferralProgram.objects.get(project=self.project)
        self.assertTrue(program.is_active)
        self.assertEqual(program.commission_value, Decimal("15"))
        self.assertEqual(program.cookie_days, 45)

    def test_unchecking_turns_it_off(self):
        ReferralProgram.objects.create(project=self.project, is_active=True)
        resp = self.client.post("/admin/marketing/referrals/", {
            "commission_type": "percent", "commission_value": "10",
            "cookie_days": "30", "minimum_payout": "0",
        })
        self.assertRedirects(resp, "/admin/marketing/referrals/")
        program = ReferralProgram.objects.get(project=self.project)
        self.assertFalse(program.is_active)

    def test_nav_lists_the_screen_under_marketing(self):
        body = self.client.get("/admin/products/").content.decode()
        self.assertIn("/admin/marketing/referrals/", body)
        self.assertIn("Referral program", body)

    def test_staff_without_manage_role_is_denied(self):
        staff = User.objects.create_user("rs", "rs@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=staff, role=StoreRole.STAFF)
        self.client.force_login(staff)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        resp = self.client.get("/admin/marketing/referrals/")
        self.assertEqual(resp.status_code, 403)


@override_settings(ALLOWED_HOSTS=["*"])
class ReferralDashboardTableTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="TableCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("to", "to@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

        self.referrer_user = User.objects.create_user("shopper", "shopper@t.test", "pw")
        self.referrer = Referrer.objects.create(project=self.project, user=self.referrer_user)
        self.order = Order.objects.create(
            project=self.project, number="ORD-1", email="buyer@t.test",
            grand_total=Decimal("1000"),
        )
        self.commission = ReferralCommission.objects.create(
            project=self.project, referrer=self.referrer, order=self.order,
            order_amount=Decimal("1000"), commission_amount=Decimal("100"),
        )

    def test_referrer_row_renders_with_stats(self):
        resp = self.client.get("/admin/marketing/referrals/")
        body = resp.content.decode()
        self.assertIn(self.referrer.code, body)
        self.assertIn("shopper@t.test", body)

    def test_pending_commission_row_renders_with_approve_reject(self):
        resp = self.client.get("/admin/marketing/referrals/")
        body = resp.content.decode()
        self.assertIn("ORD-1", body)
        self.assertIn(f"/admin/marketing/referrals/{self.commission.pk}/approve/", body)
        self.assertIn(f"/admin/marketing/referrals/{self.commission.pk}/reject/", body)

    def test_approve_commission(self):
        resp = self.client.post(f"/admin/marketing/referrals/{self.commission.pk}/approve/")
        self.assertRedirects(resp, "/admin/marketing/referrals/")
        self.commission.refresh_from_db()
        self.assertEqual(self.commission.status, CommissionStatus.APPROVED)

    def test_reject_commission(self):
        resp = self.client.post(f"/admin/marketing/referrals/{self.commission.pk}/reject/")
        self.assertRedirects(resp, "/admin/marketing/referrals/")
        self.commission.refresh_from_db()
        self.assertEqual(self.commission.status, CommissionStatus.REJECTED)

    def test_mark_paid_requires_approved_first(self):
        self.client.post(f"/admin/marketing/referrals/{self.commission.pk}/paid/")
        self.commission.refresh_from_db()
        self.assertEqual(self.commission.status, CommissionStatus.PENDING)  # no-op

        self.client.post(f"/admin/marketing/referrals/{self.commission.pk}/approve/")
        self.client.post(f"/admin/marketing/referrals/{self.commission.pk}/paid/")
        self.commission.refresh_from_db()
        self.assertEqual(self.commission.status, CommissionStatus.PAID)

    def test_toggle_referrer_disables_and_re_enables(self):
        url = f"/admin/marketing/referrals/referrer/{self.referrer.pk}/toggle/"
        self.client.post(url)
        self.referrer.refresh_from_db()
        self.assertEqual(self.referrer.status, ReferrerStatus.DISABLED)

        self.client.post(url)
        self.referrer.refresh_from_db()
        self.assertEqual(self.referrer.status, ReferrerStatus.ACTIVE)

    def test_cant_act_on_another_stores_commission(self):
        other = Project.objects.create(name="OtherCo", status="active")
        other_referrer = Referrer.objects.create(
            project=other, user=User.objects.create_user("ou", "ou@t.test", "pw"),
        )
        other_order = Order.objects.create(
            project=other, number="ORD-2", email="x@t.test", grand_total=Decimal("500"),
        )
        other_commission = ReferralCommission.objects.create(
            project=other, referrer=other_referrer, order=other_order,
            order_amount=Decimal("500"), commission_amount=Decimal("50"),
        )
        resp = self.client.post(f"/admin/marketing/referrals/{other_commission.pk}/approve/")
        self.assertEqual(resp.status_code, 404)

    def test_csv_export(self):
        resp = self.client.get("/admin/marketing/referrals/export.csv")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv")
        body = resp.content.decode()
        self.assertIn("ORD-1", body)
        self.assertIn(self.referrer.code, body)
