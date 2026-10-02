"""CRM / daily reporting."""

import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Membership, PlatformRole, Profile, StoreRole
from apps.billing import services as billing
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.crm import services as svc
from apps.crm.models import (
    Activity,
    ActivityKind,
    ActivityOutcome,
    Collection,
    CollectionMode,
    CollectionStatus,
    Lead,
    LeadStatus,
    StoreAssignment,
    StoreWorkRequest,
    Task,
    TrainingLog,
    TrainingStatus,
    WorkKind,
    WorkRequestStatus,
)
from apps.projects.models import Project
from apps.projects.services import projects_for_user

User = get_user_model()


def _dgc(name):
    u = User.objects.create_user(name, f"{name}@t.test", "pw", is_staff=True, first_name=name)
    Profile.objects.filter(user=u).update(platform_role=PlatformRole.MANAGER)
    return User.objects.get(pk=u.pk)


@override_settings(ALLOWED_HOSTS=["*"])
class CrmBase(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser("root", "r@t.test", "pw")
        self.a, self.b = _dgc("anil"), _dgc("bina")
        self.store = Project.objects.create(name="ShopCo", status="active",
                                            feature_flags={"onboarded": True})
        self.owner = User.objects.create_user("own", "own@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.store, user=self.owner, role=StoreRole.OWNER)

    def _managed(self):
        """Store's subscription (auto-created by signal) managed by DGC ``a``."""
        sub = billing.ensure_subscription(Project.objects.get(pk=self.store.pk))
        sub.manager = self.a
        sub.save(update_fields=["manager"])
        return sub

    def login(self, user, store=False):
        self.client.force_login(user)
        if store:
            s = self.client.session
            s[ACTIVE_PROJECT_SESSION_KEY] = self.store.pk
            s.save()


class ReportingTests(CrmBase):
    def test_numbers_per_person(self):
        lead = Lead.objects.create(name="L", assigned_to=self.a)
        for _ in range(3):
            svc.log_activity(actor=self.a, kind=ActivityKind.CALL, outcome=ActivityOutcome.CONNECTED, lead=lead)
        svc.log_activity(actor=self.a, kind=ActivityKind.CALL, outcome=ActivityOutcome.NO_ANSWER)
        svc.log_activity(actor=self.a, kind=ActivityKind.DEMO, outcome="done", lead=lead)
        svc.log_activity(actor=self.b, kind=ActivityKind.PRODUCT_ENTRY, count=25)
        rows, totals = svc.person_numbers(timezone.localdate())
        by = {r["user"].pk: r for r in rows}
        self.assertEqual(by[self.a.pk]["calls"], 4)
        self.assertEqual(by[self.a.pk]["connected"], 3)
        self.assertEqual(by[self.a.pk]["demos"], 1)
        self.assertEqual(by[self.b.pk]["products"], 25)
        self.assertEqual(totals["calls"], 4)
        self.assertEqual(totals["connect_pct"], 75)

    def test_demo_advances_lead(self):
        lead = Lead.objects.create(name="L", status=LeadStatus.INTERESTED)
        svc.log_activity(actor=self.a, kind=ActivityKind.DEMO, lead=lead)
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.DEMO_DONE)

    def test_not_interested_closes_lead(self):
        lead = Lead.objects.create(name="L")
        svc.log_activity(actor=self.a, kind=ActivityKind.CALL,
                         outcome=ActivityOutcome.NOT_INTERESTED, lead=lead)
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.LOST)

    def test_only_verified_money_counts(self):
        today = timezone.localdate()
        Collection.objects.create(collected_by=self.a, amount=Decimal("1000"), mode=CollectionMode.UPI,
                                  reference="u1", status=CollectionStatus.VERIFIED)
        Collection.objects.create(collected_by=self.a, amount=Decimal("500"), mode=CollectionMode.CASH,
                                  reference="c1")
        rows, totals = svc.person_numbers(today)
        self.assertEqual(totals["collection"], Decimal("1000"))
        self.assertEqual(totals["collection_pending"], Decimal("500"))

    def test_training_counts_only_when_confirmed(self):
        t = TrainingLog.objects.create(trainee=self.b, trainer=self.a)
        self.assertEqual(svc.person_numbers(timezone.localdate())[1]["trained"], 0)
        svc.respond_training(t, actor=self.a, ok=True)
        rows, totals = svc.person_numbers(timezone.localdate())
        self.assertEqual(totals["trained"], 1)
        by = {r["user"].pk: r for r in rows}
        self.assertEqual(by[self.a.pk]["trained_others"], 1)

    def test_invoice_paid_logs_verified_collection_to_dgc(self):
        sub = self._managed()
        inv = billing.issue_invoice(sub)
        billing.mark_invoice_paid(inv)
        c = Collection.objects.get(invoice=inv)
        self.assertEqual(c.collected_by, self.a)
        self.assertEqual(c.status, CollectionStatus.VERIFIED)
        billing.mark_invoice_paid(inv)  # idempotent
        self.assertEqual(Collection.objects.filter(invoice=inv).count(), 1)

    def test_no_dgc_no_collection(self):
        billing.ensure_subscription(self.store)
        inv = billing.issue_invoice(Project.objects.get(pk=self.store.pk).subscription)
        billing.mark_invoice_paid(inv)
        self.assertFalse(Collection.objects.exists())


class ScreenTests(CrmBase):
    def test_board_admin_only(self):
        url = reverse("control:crm_board")
        self.login(self.a)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.login(self.admin)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Team report")

    def test_all_screens_render(self):
        Lead.objects.create(name="Zed", assigned_to=self.a)
        names = ["crm_my_day", "crm_leads", "crm_tasks", "crm_collections", "crm_training", "crm_work"]
        for who in (self.admin, self.a):
            self.login(who)
            for n in names:
                self.assertEqual(self.client.get(reverse(f"control:{n}")).status_code, 200, (who, n))
        self.login(self.admin)
        for n in ("crm_board", "crm_targets"):
            self.assertEqual(self.client.get(reverse(f"control:{n}")).status_code, 200, n)
        self.assertEqual(self.client.get(reverse("control:crm_board") + "?range=month&active=1").status_code, 200)

    def test_owner_cannot_reach_crm(self):
        self.login(self.owner, store=True)
        for n in ("crm_my_day", "crm_leads", "crm_collections"):
            self.assertEqual(self.client.get(reverse(f"control:{n}")).status_code, 403, n)

    def test_dgc_sees_only_own_leads(self):
        mine = Lead.objects.create(name="Mine", assigned_to=self.a)
        theirs = Lead.objects.create(name="Theirs", assigned_to=self.b)
        self.login(self.a)
        r = self.client.get(reverse("control:crm_leads"))
        self.assertContains(r, "Mine")
        self.assertNotContains(r, "Theirs")
        self.assertEqual(self.client.get(reverse("control:crm_lead", kwargs={"pk": theirs.pk})).status_code, 404)
        self.assertEqual(self.client.get(reverse("control:crm_lead", kwargs={"pk": mine.pk})).status_code, 200)

    def test_quick_log_and_isolation(self):
        lead = Lead.objects.create(name="Mine", assigned_to=self.a)
        other = Lead.objects.create(name="Theirs", assigned_to=self.b)
        self.login(self.a)
        r = self.client.post(reverse("control:crm_log"),
                             {"kind": "call", "outcome": "connected", "lead": lead.pk})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Activity.objects.filter(actor=self.a, kind="call").count(), 1)
        r = self.client.post(reverse("control:crm_log"), {"kind": "call", "lead": other.pk})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.client.post(reverse("control:crm_log"), {"kind": "nope"}).status_code, 404)

    def test_bulk_assign_admin_only(self):
        lead = Lead.objects.create(name="L")
        self.login(self.a)
        self.assertEqual(self.client.post(reverse("control:crm_lead_assign"),
                                          {"ids": [lead.pk], "assignee": self.a.pk}).status_code, 403)
        self.login(self.admin)
        self.client.post(reverse("control:crm_lead_assign"), {"ids": [lead.pk], "assignee": self.b.pk})
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.b)

    def test_task_assign_and_done(self):
        self.login(self.admin)
        self.client.post(reverse("control:crm_task_create"), {"title": "Call 20", "assignee": self.a.pk})
        t = Task.objects.get()
        self.login(self.b)
        self.assertEqual(self.client.post(reverse("control:crm_task_done", kwargs={"pk": t.pk})).status_code, 404)
        self.login(self.a)
        self.client.post(reverse("control:crm_task_done", kwargs={"pk": t.pk}))
        t.refresh_from_db()
        self.assertEqual(t.status, "done")

    def test_offline_collection_flow(self):
        self.login(self.a)
        r = self.client.post(reverse("control:crm_collection_create"),
                             {"amount": "2999", "mode": "upi", "reference": "UTR123",
                              "collected_on": timezone.localdate().isoformat()})
        self.assertEqual(r.status_code, 302)
        c = Collection.objects.get()
        self.assertEqual(c.status, CollectionStatus.UNVERIFIED)
        self.assertEqual(self.client.post(reverse("control:crm_collection_verify", kwargs={"pk": c.pk})).status_code, 403)
        self.login(self.admin)
        self.client.post(reverse("control:crm_collection_verify", kwargs={"pk": c.pk}), {"action": "verify"})
        c.refresh_from_db()
        self.assertEqual(c.status, CollectionStatus.VERIFIED)

    def test_collection_needs_reference_and_positive_amount(self):
        self.login(self.a)
        self.client.post(reverse("control:crm_collection_create"), {"amount": "0", "mode": "cash", "reference": "x"})
        self.client.post(reverse("control:crm_collection_create"), {"amount": "5", "mode": "cash", "reference": ""})
        self.assertFalse(Collection.objects.exists())

    def test_training_trainer_confirms(self):
        self.login(self.b)
        self.client.post(reverse("control:crm_training_create"),
                         {"trainer": self.a.pk, "topic": "Onboarding", "trained_on": timezone.localdate().isoformat()})
        t = TrainingLog.objects.get()
        self.assertEqual((t.trainee, t.trainer, t.status), (self.b, self.a, TrainingStatus.PENDING))
        # trainee cannot self-confirm
        self.assertEqual(self.client.post(reverse("control:crm_training_respond", kwargs={"pk": t.pk}),
                                          {"action": "confirm"}).status_code, 403)
        self.login(self.a)
        self.client.post(reverse("control:crm_training_respond", kwargs={"pk": t.pk}), {"action": "confirm"})
        t.refresh_from_db()
        self.assertEqual(t.status, TrainingStatus.CONFIRMED)

    def test_cannot_name_self_as_trainer(self):
        self.login(self.b)
        self.client.post(reverse("control:crm_training_create"), {"trainer": self.b.pk})
        self.assertFalse(TrainingLog.objects.exists())


class StoreWorkTests(CrmBase):
    def setUp(self):
        super().setUp()
        self._managed()

    def test_request_assign_grants_scoped_access(self):
        self.assertNotIn(self.store, projects_for_user(self.b))
        wr = svc.request_store_work(project=self.store, requested_by=self.owner, kind=WorkKind.CATALOG, note="50 items")
        self.login(self.admin)
        self.client.post(reverse("control:crm_work_assign", kwargs={"pk": wr.pk}), {"assignee": self.b.pk})
        wr.refresh_from_db()
        self.assertEqual((wr.status, wr.assigned_to), (WorkRequestStatus.ASSIGNED, self.b))
        self.assertIn(self.store, projects_for_user(self.b))
        # assignment never makes them the commission manager
        self.assertNotEqual(self.store.subscription.manager_id, self.b.pk)

    def test_dgc_cannot_assign_or_self_claim(self):
        wr = svc.request_store_work(project=self.store, requested_by=self.owner, kind=WorkKind.CATALOG)
        self.login(self.b)
        self.assertEqual(self.client.post(reverse("control:crm_work_assign", kwargs={"pk": wr.pk}),
                                          {"assignee": self.b.pk}).status_code, 403)
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequestStatus.OPEN)
        self.assertNotIn(self.store, projects_for_user(self.b))

    def test_assignee_blocked_from_orders_and_money(self):
        wr = svc.request_store_work(project=self.store, requested_by=self.owner, kind=WorkKind.CATALOG)
        svc.assign_store_work(wr, self.b, actor=self.admin)
        self.login(self.b, store=True)
        self.assertEqual(self.client.get(reverse("control:product_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("control:order_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("control:customers")).status_code, 403)
        self.assertEqual(self.client.get(reverse("control:team")).status_code, 403)
        self.assertEqual(self.client.get(reverse("control:payment_providers")).status_code, 403)

    def test_revoke_removes_access(self):
        wr = svc.request_store_work(project=self.store, requested_by=self.owner, kind=WorkKind.CATALOG)
        sa = svc.assign_store_work(wr, self.b, actor=self.admin)
        svc.revoke_assignment(sa, actor=self.admin)
        self.assertNotIn(self.store, projects_for_user(self.b))

    def test_owner_can_request_and_revoke(self):
        self.login(self.owner, store=True)
        self.client.post(reverse("control:crm_owner_help"), {"kind": "catalog", "note": "help"})
        wr = StoreWorkRequest.objects.get()
        self.assertEqual(wr.requested_by, self.owner)
        sa = svc.assign_store_work(wr, self.b, actor=self.admin)
        self.client.post(reverse("control:crm_owner_help"), {"revoke": sa.pk})
        sa.refresh_from_db()
        self.assertFalse(sa.is_active)
        self.assertEqual(self.client.get(reverse("control:crm_owner_help")).status_code, 200)

    def test_owner_cannot_revoke_other_store_assignment(self):
        other = Project.objects.create(name="Other", status="active")
        wr = svc.request_store_work(project=other, requested_by=self.admin, kind=WorkKind.CATALOG)
        sa = svc.assign_store_work(wr, self.b, actor=self.admin)
        self.login(self.owner, store=True)
        self.assertEqual(self.client.post(reverse("control:crm_owner_help"), {"revoke": sa.pk}).status_code, 404)

    def test_mark_done_logs_activity_and_ends_access(self):
        wr = svc.request_store_work(project=self.store, requested_by=self.owner, kind=WorkKind.CATALOG)
        svc.assign_store_work(wr, self.b, actor=self.admin)
        self.login(self.b)
        self.client.post(reverse("control:crm_work_done", kwargs={"pk": wr.pk}))
        wr.refresh_from_db()
        self.assertEqual(wr.status, WorkRequestStatus.DONE)
        self.assertFalse(StoreAssignment.objects.filter(is_active=True).exists())
        self.assertTrue(Activity.objects.filter(actor=self.b, kind=ActivityKind.STORE_SETUP).exists())


class CsrfRenderTests(CrmBase):
    """Macros can't see template globals — the quick-log form must still carry
    a CSRF token (the plain test client skips CSRF, so check the HTML)."""

    def _has_token(self, url):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.a)
        html = c.get(url).content.decode()
        form = html.split('action="%s"' % reverse("control:crm_log"))[1].split("</form>")[0]
        self.assertIn('name="csrfmiddlewaretoken"', form)

    def test_my_day_quicklog_has_token(self):
        self._has_token(reverse("control:crm_my_day"))

    def test_lead_quicklog_has_token(self):
        lead = Lead.objects.create(name="L", assigned_to=self.a)
        self._has_token(reverse("control:crm_lead", kwargs={"pk": lead.pk}))

    def test_quicklog_post_passes_csrf_with_token(self):
        import re
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.a)
        html = c.get(reverse("control:crm_my_day")).content.decode()
        tok = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
        r = c.post(reverse("control:crm_log"), {"kind": "call", "outcome": "connected",
                                                 "csrfmiddlewaretoken": tok})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Activity.objects.filter(actor=self.a).count(), 1)
