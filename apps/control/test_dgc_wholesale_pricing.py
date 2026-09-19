"""DGC wholesale pricing: a store owner sees/pays the retail price
(Plan.price_monthly/yearly) on their own dashboard and the marketing page;
a DGC who provisions a client's store instead pays the platform the lower
wholesale price (Plan.dgc_price_monthly/yearly), set automatically as
Subscription.override_price by store_services.create_store, and keeps the
retail/wholesale spread as their own margin, arranged with their client
outside the platform. Subscription.billed_to_dgc marks that relationship so
_accrue_commission never also pays the DGC a commission on top of a price
they themselves just paid — a referral-only relationship (Subscription
.referred_by, no wholesale override) is a different, still-commissioned
path and untouched by any of this."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.billing import services as billing_svc
from apps.billing.models import BillingPeriod, ManagerCommission, Plan
from apps.control import store_services
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


class PlanDgcPriceHelperTests(TestCase):
    def test_dgc_price_for_period(self):
        plan = Plan.objects.create(
            code="test-tier", name="Test Tier",
            price_monthly=Decimal("100"), price_yearly=Decimal("1000"),
            dgc_price_monthly=Decimal("60"), dgc_price_yearly=Decimal("600"),
        )
        self.assertEqual(plan.dgc_price_for(BillingPeriod.MONTHLY), Decimal("60"))
        self.assertEqual(plan.dgc_price_for(BillingPeriod.YEARLY), Decimal("600"))


class NewPlanPricingMigrationTests(TestCase):
    """The 0014 data migration's numbers, checked against the actual rows a
    fresh test database ends up with after every migration runs."""

    def test_basic_pricing(self):
        p = Plan.objects.get(code="basic")
        self.assertEqual(p.price_yearly, Decimal("24999"))
        self.assertEqual(p.price_monthly, Decimal("2499.90"))
        self.assertEqual(p.dgc_price_yearly, Decimal("14999"))
        self.assertEqual(p.dgc_price_monthly, Decimal("1499.90"))

    def test_growth_pricing(self):
        p = Plan.objects.get(code="growth")
        self.assertEqual(p.price_yearly, Decimal("34999"))
        self.assertEqual(p.price_monthly, Decimal("3499.90"))
        self.assertEqual(p.dgc_price_yearly, Decimal("24999"))
        self.assertEqual(p.dgc_price_monthly, Decimal("2499.90"))

    def test_pro_pricing(self):
        p = Plan.objects.get(code="pro")
        self.assertEqual(p.price_yearly, Decimal("64999"))
        self.assertEqual(p.price_monthly, Decimal("6499.90"))
        self.assertEqual(p.dgc_price_yearly, Decimal("54999"))
        self.assertEqual(p.dgc_price_monthly, Decimal("5499.90"))

    def test_features_and_limits_untouched(self):
        p = Plan.objects.get(code="basic")
        self.assertEqual(p.max_products, 250)
        self.assertIn("Full storefront", p.features)


@override_settings(ALLOWED_HOSTS=["*"])
class CreateStoreWholesalePricingTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.get(code="basic")
        self.admin = User.objects.create_superuser("root", "root@t.test", "pw")
        self.dgc = User.objects.create_user("dgc", "dgc@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=self.dgc).update(platform_role=PlatformRole.MANAGER)
        self.dgc = User.objects.get(pk=self.dgc.pk)

    def test_dgc_provisioned_store_is_billed_wholesale(self):
        project, owner, created, temp_pw = store_services.create_store(
            name="Client Store", owner_email="client@t.test", plan=self.plan,
            actor=self.dgc, manager=self.dgc, period=BillingPeriod.YEARLY,
        )
        sub = project.subscription
        self.assertTrue(sub.billed_to_dgc)
        self.assertEqual(sub.override_price, Decimal("14999"))
        self.assertEqual(sub.current_price(), Decimal("14999"))

    def test_dgc_provisioned_store_monthly(self):
        project, *_ = store_services.create_store(
            name="Client Store 2", owner_email="client2@t.test", plan=self.plan,
            actor=self.dgc, manager=self.dgc, period=BillingPeriod.MONTHLY,
        )
        sub = project.subscription
        self.assertTrue(sub.billed_to_dgc)
        self.assertEqual(sub.override_price, Decimal("1499.90"))

    def test_admin_created_store_with_no_manager_is_retail(self):
        project, *_ = store_services.create_store(
            name="Direct Store", owner_email="direct@t.test", plan=self.plan,
            actor=self.admin, manager=None, period=BillingPeriod.YEARLY,
        )
        sub = project.subscription
        self.assertFalse(sub.billed_to_dgc)
        self.assertIsNone(sub.override_price)
        self.assertEqual(sub.current_price(), Decimal("24999"))

    def test_admin_created_store_with_a_manager_is_still_wholesale(self):
        # An admin hand-picking a DGC on the create form is provisioning on
        # that DGC's behalf -- same wholesale rule applies.
        project, *_ = store_services.create_store(
            name="Partner Store", owner_email="partner@t.test", plan=self.plan,
            actor=self.admin, manager=self.dgc, period=BillingPeriod.YEARLY,
        )
        sub = project.subscription
        self.assertTrue(sub.billed_to_dgc)
        self.assertEqual(sub.override_price, Decimal("14999"))


@override_settings(ALLOWED_HOSTS=["*"])
class CommissionAccrualSkipTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.get(code="basic")
        self.dgc = User.objects.create_user("cdgc", "cdgc@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=self.dgc).update(platform_role=PlatformRole.MANAGER)
        self.dgc = User.objects.get(pk=self.dgc.pk)

    def _issue_and_pay(self, sub):
        inv = billing_svc.issue_invoice(sub, period_start=sub.current_period_start)
        return billing_svc.mark_invoice_paid(inv)

    def test_wholesale_billed_subscription_accrues_no_commission(self):
        project = Project.objects.create(name="WholesaleCo", status="active")
        sub = billing_svc.ensure_subscription(project, plan=self.plan, manager=self.dgc)
        sub.override_price = self.plan.dgc_price_yearly
        sub.billed_to_dgc = True
        sub.save(update_fields=["override_price", "billed_to_dgc"])

        invoice = self._issue_and_pay(sub)
        self.assertFalse(ManagerCommission.objects.filter(invoice=invoice).exists())

    def test_referral_only_subscription_still_accrues_commission(self):
        project = Project.objects.create(name="ReferredCo", status="active")
        sub = billing_svc.ensure_subscription(project, plan=self.plan)
        sub.referred_by = self.dgc
        sub.save(update_fields=["referred_by"])

        invoice = self._issue_and_pay(sub)
        commission = ManagerCommission.objects.get(invoice=invoice)
        self.assertEqual(commission.manager_id, self.dgc.pk)
        self.assertEqual(commission.base_amount, invoice.amount)

    def test_non_wholesale_manager_managed_subscription_still_accrues_commission(self):
        # A DGC hand-assigned to an existing store via set_store_manager
        # (not provisioned wholesale) keeps earning the normal % commission.
        project = Project.objects.create(name="LegacyManagedCo", status="active")
        sub = billing_svc.ensure_subscription(project, plan=self.plan)
        sub.manager = self.dgc
        sub.save(update_fields=["manager"])

        invoice = self._issue_and_pay(sub)
        commission = ManagerCommission.objects.get(invoice=invoice)
        self.assertEqual(commission.manager_id, self.dgc.pk)


@override_settings(ALLOWED_HOSTS=["*"])
class StoreCreateFormPlanLabelTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.get(code="basic")
        self.admin = User.objects.create_superuser("proot", "proot@t.test", "pw")
        self.dgc = User.objects.create_user("pdgc", "pdgc@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=self.dgc).update(platform_role=PlatformRole.MANAGER)

    def test_admin_sees_retail_price_only(self):
        self.client.force_login(self.admin)
        body = self.client.get("/admin/stores/new/").content.decode()
        self.assertIn("₹24,999/yr", body)
        self.assertNotIn("you pay", body)

    def test_dgc_sees_wholesale_and_retail_price(self):
        self.client.force_login(self.dgc)
        body = self.client.get("/admin/stores/new/").content.decode()
        self.assertIn("₹14,999/yr you pay", body)
        self.assertIn("₹24,999/yr retail", body)


@override_settings(ALLOWED_HOSTS=["*"])
class DgcDashboardPricingPanelTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("droot", "droot@t.test", "pw")
        self.dgc = User.objects.create_user("ddgc", "ddgc@t.test", "pw", is_staff=True)
        Profile.objects.filter(user=self.dgc).update(platform_role=PlatformRole.MANAGER)

    def test_dgc_sees_pricing_panel_on_their_dashboard(self):
        self.client.force_login(self.dgc)
        body = self.client.get("/admin/").content.decode()
        self.assertIn("Your B2B pricing", body)
        self.assertIn("₹14999/yr", body)
        self.assertIn("₹24999/yr", body)

    def test_admin_does_not_see_the_dgc_pricing_panel(self):
        self.client.force_login(self.admin)
        body = self.client.get("/admin/").content.decode()
        self.assertNotIn("Your B2B pricing", body)

    def test_owner_with_a_store_does_not_see_the_dgc_pricing_panel(self):
        project = Project.objects.create(
            name="OwnerDashCo", status="active", feature_flags={"onboarded": True},
        )
        billing_svc.ensure_subscription(project)
        owner = User.objects.create_user("dow", "dow@t.test", "pw", is_staff=True)
        Membership.objects.create(project=project, user=owner, role=StoreRole.OWNER)
        self.client.force_login(owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = project.pk
        s.save()
        body = self.client.get("/admin/").content.decode()
        self.assertNotIn("Your B2B pricing", body)
