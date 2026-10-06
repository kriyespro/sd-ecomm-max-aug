"""CRM / daily reporting."""

import datetime as dt
import json
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
        self.assertContains(r, "Team board")

    def test_all_screens_render(self):
        Lead.objects.create(name="Zed", assigned_to=self.a)
        names = ["crm_my_day", "crm_lead_board", "crm_tasks", "crm_collections", "crm_training", "crm_work"]
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
        r = self.client.get(reverse("control:crm_leads") + "?view=list")
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
        Lead.objects.create(name="Q", assigned_to=self.a)
        self._has_token(reverse("control:crm_my_day"))

    def test_lead_quicklog_has_token(self):
        lead = Lead.objects.create(name="L", assigned_to=self.a)
        self._has_token(reverse("control:crm_lead", kwargs={"pk": lead.pk}))

    def test_quicklog_post_passes_csrf_with_token(self):
        import re
        from django.test import Client
        Lead.objects.create(name="Q", assigned_to=self.a)
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.a)
        html = c.get(reverse("control:crm_my_day")).content.decode()
        tok = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
        r = c.post(reverse("control:crm_log"), {"kind": "call", "outcome": "connected",
                                                 "csrfmiddlewaretoken": tok})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Activity.objects.filter(actor=self.a).count(), 1)


class QueueTests(CrmBase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()

    def _q(self, **kw):
        return list(svc.call_queue(self.a, **kw))

    def test_order_due_followups_first_then_fresh(self):
        fresh = Lead.objects.create(name="fresh", assigned_to=self.a)
        due = Lead.objects.create(name="due", assigned_to=self.a,
                                  next_follow_up=self.today - dt.timedelta(days=2))
        future = Lead.objects.create(name="future", assigned_to=self.a,
                                     next_follow_up=self.today + dt.timedelta(days=3))
        Lead.objects.create(name="other", assigned_to=self.b)
        Lead.objects.create(name="closed", assigned_to=self.a, status=LeadStatus.LOST)
        self.assertEqual(self._q(), [due, fresh])
        self.assertNotIn(future, self._q())

    def test_skip_excludes(self):
        l1 = Lead.objects.create(name="a", assigned_to=self.a)
        l2 = Lead.objects.create(name="b", assigned_to=self.a)
        self.assertEqual(self._q(skip=[l1.pk]), [l2])

    def test_worked_today_drops_out(self):
        l = Lead.objects.create(name="a", assigned_to=self.a)
        svc.log_activity(actor=self.a, kind="call", outcome="connected", lead=l)
        self.assertEqual(self._q(), [])

    def test_no_answer_comes_back_tomorrow(self):
        l = Lead.objects.create(name="a", assigned_to=self.a)
        svc.log_activity(actor=self.a, kind="call", outcome="no_answer", lead=l)
        l.refresh_from_db()
        self.assertEqual(l.next_follow_up, self.today + dt.timedelta(days=1))
        self.assertEqual(self._q(), [])

    def test_unreachable_two_days_and_explicit_follow_up_wins(self):
        l = Lead.objects.create(name="a", assigned_to=self.a)
        svc.log_activity(actor=self.a, kind="call", outcome="not_reachable", lead=l)
        l.refresh_from_db()
        self.assertEqual(l.next_follow_up, self.today + dt.timedelta(days=2))
        svc.log_activity(actor=self.a, kind="call", outcome="no_answer", lead=l,
                         follow_up=self.today + dt.timedelta(days=7))
        l.refresh_from_db()
        self.assertEqual(l.next_follow_up, self.today + dt.timedelta(days=7))

    def test_worked_followup_is_cleared(self):
        l = Lead.objects.create(name="a", assigned_to=self.a, next_follow_up=self.today)
        svc.log_activity(actor=self.a, kind="call", outcome="connected", lead=l)
        l.refresh_from_db()
        self.assertIsNone(l.next_follow_up)
        self.assertEqual(self._q(), [])


class NewUxTests(CrmBase):
    def test_follow_in_chip_sets_date(self):
        l = Lead.objects.create(name="a", assigned_to=self.a)
        self.login(self.a)
        self.client.post(reverse("control:crm_log"),
                         {"kind": "call", "outcome": "connected", "lead": l.pk, "follow_in": "3"})
        l.refresh_from_db()
        self.assertEqual(l.next_follow_up, timezone.localdate() + dt.timedelta(days=3))

    def test_my_day_shows_next_lead_and_skip(self):
        a = Lead.objects.create(name="Alpha Lead", assigned_to=self.a, phone="9876543210")
        Lead.objects.create(name="Beta Lead", assigned_to=self.a)
        self.login(self.a)
        r = self.client.get(reverse("control:crm_my_day"))
        self.assertContains(r, "Alpha Lead")
        self.assertContains(r, "tel:9876543210")
        self.assertContains(r, "wa.me/919876543210")
        r = self.client.get(reverse("control:crm_my_day") + f"?skip={a.pk}")
        self.assertContains(r, "Beta Lead")
        self.assertNotContains(r, "Alpha Lead")

    def test_my_day_empty_queue(self):
        self.login(self.a)
        self.assertContains(self.client.get(reverse("control:crm_my_day")), "Queue clear")

    def test_status_stepper_and_won_guard(self):
        l = Lead.objects.create(name="a", assigned_to=self.a)
        other = Lead.objects.create(name="b", assigned_to=self.b)
        self.login(self.a)
        self.client.post(reverse("control:crm_lead_status", kwargs={"pk": l.pk}), {"status": "interested"})
        l.refresh_from_db()
        self.assertEqual(l.status, LeadStatus.INTERESTED)
        self.client.post(reverse("control:crm_lead_status", kwargs={"pk": l.pk}), {"status": "won"})
        l.refresh_from_db()
        self.assertEqual(l.status, LeadStatus.INTERESTED)  # won only via the store flow
        self.assertEqual(self.client.post(reverse("control:crm_lead_status", kwargs={"pk": other.pk}),
                                          {"status": "lost"}).status_code, 404)

    def test_tabs_role_aware_and_pink_theme(self):
        self.login(self.a)
        html = self.client.get(reverse("control:crm_lead_board")).content.decode()
        self.assertIn("My day", html)
        self.assertNotIn('href="%s"' % reverse("control:crm_board"), html.split("CRM sections")[1].split("</nav>")[0])
        self.assertIn("bg-pink-600", html)
        self.login(self.admin)
        html = self.client.get(reverse("control:crm_lead_board")).content.decode()
        self.assertIn('href="%s"' % reverse("control:crm_board"), html.split("CRM sections")[1].split("</nav>")[0])

    def test_wa_number(self):
        self.assertEqual(Lead(phone="98765 43210").wa_number, "919876543210")
        self.assertEqual(Lead(phone="+91 98765 43210").wa_number, "919876543210")
        self.assertEqual(Lead(phone="").wa_number, "")


class SidebarTests(TestCase):
    """The sidebar carries a single CRM entry; the in-page tabs carry the rest."""

    def _names(self, *, admin):
        from apps.control.navigation import build_nav
        nav = build_nav(platform_staff=True, platform_admin=admin, active_project=None,
                        can_manage=False, can_upload_skin=False)
        return [i["name"] for sec in nav for i in sec["items"] if i["name"].startswith("crm_")]

    def test_admin_sees_only_board(self):
        self.assertEqual(self._names(admin=True), ["crm_board"])

    def test_dgc_sees_only_my_day_entry(self):
        self.assertEqual(self._names(admin=False), ["crm_my_day"])


class BoardDefaultTests(CrmBase):
    def test_default_hides_idle_and_toggle_shows_all(self):
        svc.log_activity(actor=self.a, kind="call", outcome="connected")
        self.login(self.admin)
        url = reverse("control:crm_board")
        r = self.client.get(url)
        self.assertContains(r, ">anil<")
        self.assertNotContains(r, ">bina<")
        self.assertContains(r, "Show everyone")
        r = self.client.get(url + "?active=0")
        self.assertContains(r, ">anil<")
        self.assertContains(r, ">bina<")
        self.assertContains(r, "Hide people with no activity")
        # the range tabs keep the chosen mode
        self.assertContains(r, "range=7d&amp;active=0")


class CompactFormTests(CrmBase):
    def test_forms_render_compact_with_toggle(self):
        self.login(self.admin)
        for n in ("crm_tasks", "crm_collections", "crm_training", "crm_work"):
            html = self.client.get(reverse(f"control:{n}")).content.decode()
            self.assertIn('id="newform"', html, n)
            self.assertIn('class="cf hidden', html, n)

    def test_task_form_has_no_lead_dropdown_and_names_people(self):
        Lead.objects.create(name="LeadXYZ")
        self.login(self.admin)
        html = self.client.get(reverse("control:crm_tasks")).content.decode()
        self.assertNotIn('name="lead"', html)
        self.assertNotIn("LeadXYZ", html)
        self.assertIn(">anil<", html)  # people shown by name, not "User object"

    def test_work_form_preselects_single_store_and_posts(self):
        self.login(self.admin)
        Project.objects.exclude(pk=self.store.pk).delete()
        html = self.client.get(reverse("control:crm_work")).content.decode()
        self.assertIn(f'value="{self.store.pk}" selected', html)
        r = self.client.post(reverse("control:crm_work_create"),
                             {"project": self.store.pk, "kind": "maintenance", "note": "fix menu"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(StoreWorkRequest.objects.get().kind, "maintenance")

    def test_collection_form_defaults_upi(self):
        self.login(self.a)
        html = self.client.get(reverse("control:crm_collections")).content.decode()
        self.assertIn('value="upi" selected', html)


class KanbanTests(CrmBase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()

    def _lead(self, name="L", who=None, **kw):
        return Lead.objects.create(name=name, assigned_to=who or self.a, **kw)

    # -- default view + cookie
    def test_leads_defaults_to_board_and_remembers_list(self):
        self.login(self.a)
        r = self.client.get(reverse("control:crm_leads"))
        self.assertRedirects(r, reverse("control:crm_lead_board"))
        self.assertEqual(self.client.get(reverse("control:crm_leads") + "?view=list").status_code, 200)
        self.assertEqual(self.client.get(reverse("control:crm_leads")).status_code, 200)  # cookie: list
        self.client.get(reverse("control:crm_lead_board"))
        self.assertEqual(self.client.get(reverse("control:crm_leads")).status_code, 302)  # cookie: board

    def test_filtered_list_url_is_not_redirected(self):
        self.login(self.a)
        self.assertEqual(self.client.get(reverse("control:crm_leads") + "?q=x").status_code, 200)
        self.assertEqual(self.client.get(reverse("control:crm_leads") + "?status=new").status_code, 200)

    # -- board content + isolation
    def test_board_shows_columns_and_only_own_leads(self):
        self._lead("Alpha Co", status=LeadStatus.INTERESTED)
        self._lead("Bravo Co", who=self.b)
        self.login(self.a)
        r = self.client.get(reverse("control:crm_lead_board"))
        self.assertContains(r, "Alpha Co")
        self.assertNotContains(r, "Bravo Co")
        for label in ("New", "Contacted", "Interested", "Demo booked", "Demo done", "Negotiating", "Won", "Lost"):
            self.assertContains(r, label)

    def test_admin_board_sees_all_and_filters_owner(self):
        self._lead("Alpha Co")
        self._lead("Bravo Co", who=self.b)
        self.login(self.admin)
        r = self.client.get(reverse("control:crm_lead_board"))
        self.assertContains(r, "Alpha Co")
        self.assertContains(r, "Bravo Co")
        r = self.client.get(reverse("control:crm_lead_board") + f"?owner={self.b.pk}")
        self.assertNotContains(r, "Alpha Co")
        self.assertContains(r, "Bravo Co")

    def test_dgc_cannot_widen_board_with_owner_param(self):
        self._lead("Bravo Co", who=self.b)
        self.login(self.a)
        r = self.client.get(reverse("control:crm_lead_board") + f"?owner={self.b.pk}")
        self.assertNotContains(r, "Bravo Co")

    def test_owner_user_blocked(self):
        self.login(self.owner, store=True)
        self.assertEqual(self.client.get(reverse("control:crm_lead_board")).status_code, 403)

    # -- ordering / stale / caps
    def test_card_order_overdue_first_then_oldest_untouched(self):
        fresh = self._lead("fresh")
        due = self._lead("due", next_follow_up=self.today - dt.timedelta(days=1))
        touched = self._lead("touched")
        svc.log_activity(actor=self.a, kind="call", outcome="connected", lead=touched)
        cols = {c["status"]: c for c in svc.board_columns(self.a)["columns"]}
        # "touched" moved to contacted by the call; fresh/due stay in new
        names = [c.name for c in cols["new"]["cards"]]
        self.assertEqual(names[0], "due")
        self.assertIn("fresh", names)

    def test_stale_flag_and_count(self):
        old = self._lead("old")
        Lead.objects.filter(pk=old.pk).update(created_at=timezone.now() - dt.timedelta(days=10))
        self._lead("new_one")
        col = {c["status"]: c for c in svc.board_columns(self.a)["columns"]}["new"]
        self.assertEqual(col["stale"], 1)
        flags = {c.name: c.stale for c in col["cards"]}
        self.assertEqual(flags, {"old": True, "new_one": False})
        old_card = [c for c in col["cards"] if c.name == "old"][0]
        self.assertGreaterEqual(old_card.idle_days, 10)

    def test_recent_contact_clears_stale(self):
        old = self._lead("old")
        Lead.objects.filter(pk=old.pk).update(created_at=timezone.now() - dt.timedelta(days=10))
        svc.log_activity(actor=self.a, kind="call", outcome="no_answer", lead=Lead.objects.get(pk=old.pk))
        cols = {c["status"]: c for c in svc.board_columns(self.a)["columns"]}
        self.assertEqual(sum(c["stale"] for c in cols.values()), 0)

    def test_column_cap_and_full(self):
        Lead.objects.bulk_create([Lead(name=f"n{i}", assigned_to=self.a) for i in range(35)])
        col = {c["status"]: c for c in svc.board_columns(self.a)["columns"]}["new"]
        self.assertEqual((len(col["cards"]), col["count"], col["more"]), (30, 35, 5))
        col = {c["status"]: c for c in svc.board_columns(self.a, full="new")["columns"]}["new"]
        self.assertEqual((len(col["cards"]), col["more"]), (35, 0))

    def test_won_lost_counts(self):
        self._lead("w", status=LeadStatus.WON)
        self._lead("l1", status=LeadStatus.LOST)
        self._lead("l2", status=LeadStatus.LOST)
        data = svc.board_columns(self.a)
        self.assertEqual((data["won"], data["lost"]), (1, 2))
        self.assertEqual(sum(len(c["cards"]) for c in data["columns"]), 0)

    # -- moving
    def test_move_endpoint_changes_stage_and_writes_history(self):
        lead = self._lead("m")
        self.login(self.a)
        r = self.client.post(reverse("control:crm_lead_move", kwargs={"pk": lead.pk}), {"status": "demo_booked"})
        self.assertEqual(r.status_code, 204)
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.DEMO_BOOKED)
        act = Activity.objects.get(lead=lead, kind=ActivityKind.STAGE)
        self.assertIn("New → Demo booked", act.note)

    def test_move_to_lost_records_reason(self):
        lead = self._lead("m")
        self.login(self.a)
        self.client.post(reverse("control:crm_lead_move", kwargs={"pk": lead.pk}),
                         {"status": "lost", "reason": "Too expensive"})
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.LOST)
        self.assertIn("Too expensive", Activity.objects.get(lead=lead).note)

    def test_cannot_drag_to_won_or_move_a_won_lead(self):
        lead = self._lead("m")
        self.login(self.a)
        r = self.client.post(reverse("control:crm_lead_move", kwargs={"pk": lead.pk}), {"status": "won"})
        self.assertEqual(r.status_code, 409)
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.NEW)
        won = self._lead("w", status=LeadStatus.WON)
        r = self.client.post(reverse("control:crm_lead_move", kwargs={"pk": won.pk}), {"status": "new"})
        self.assertEqual(r.status_code, 409)

    def test_bad_stage_and_foreign_lead(self):
        mine = self._lead("m")
        theirs = self._lead("t", who=self.b)
        self.login(self.a)
        self.assertEqual(self.client.post(reverse("control:crm_lead_move", kwargs={"pk": mine.pk}),
                                          {"status": "nonsense"}).status_code, 409)
        self.assertEqual(self.client.post(reverse("control:crm_lead_move", kwargs={"pk": theirs.pk}),
                                          {"status": "contacted"}).status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, LeadStatus.NEW)

    def test_move_requires_post(self):
        lead = self._lead("m")
        self.login(self.a)
        self.assertEqual(self.client.get(reverse("control:crm_lead_move", kwargs={"pk": lead.pk})).status_code, 405)

    def test_move_enforces_csrf(self):
        from django.test import Client
        lead = self._lead("m")
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.a)
        r = c.post(reverse("control:crm_lead_move", kwargs={"pk": lead.pk}), {"status": "contacted"})
        self.assertEqual(r.status_code, 403)

    def test_board_page_carries_csrf_token_for_js(self):
        from django.test import Client
        self._lead("m")
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.a)
        html = c.get(reverse("control:crm_lead_board")).content.decode()
        self.assertIn('name="csrfmiddlewaretoken"', html.split('x-data="leadBoard()"')[1])

    def test_stage_changed_at_tracks_stage_moves_only(self):
        lead = self._lead("m")
        first = lead.stage_changed_at
        self.assertIsNotNone(first)
        lead = Lead.objects.get(pk=lead.pk)
        lead.notes = "x"
        lead.save()
        lead.refresh_from_db()
        self.assertEqual(lead.stage_changed_at, first)
        svc.move_lead(lead, "interested", actor=self.a)
        lead.refresh_from_db()
        self.assertGreater(lead.stage_changed_at, first)

    def test_stepper_writes_history_and_blocks_won(self):
        lead = self._lead("m")
        self.login(self.a)
        self.client.post(reverse("control:crm_lead_status", kwargs={"pk": lead.pk}), {"status": "interested"})
        self.assertTrue(Activity.objects.filter(lead=lead, kind=ActivityKind.STAGE).exists())
        self.client.post(reverse("control:crm_lead_status", kwargs={"pk": lead.pk}), {"status": "won"})
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.INTERESTED)

    def test_quick_add_from_board_returns_to_board(self):
        self.login(self.a)
        r = self.client.post(reverse("control:crm_lead_create"),
                             {"name": "Quick", "phone": "9000000000", "next": reverse("control:crm_lead_board")})
        self.assertRedirects(r, reverse("control:crm_lead_board"))
        self.assertEqual(Lead.objects.get(name="Quick").assigned_to, self.a)
        # open-redirect guard
        r = self.client.post(reverse("control:crm_lead_create"), {"name": "Q2", "next": "https://evil.test/"})
        self.assertNotIn("evil.test", r["Location"])

    def test_stage_moves_do_not_count_as_calls(self):
        lead = self._lead("m")
        svc.move_lead(lead, "contacted", actor=self.a)
        rows, totals = svc.person_numbers(timezone.localdate())
        self.assertEqual(totals["calls"], 0)

    def test_quick_log_from_board_menu_endpoint(self):
        lead = self._lead("m")
        self.login(self.a)
        r = self.client.post(reverse("control:crm_log"), {"kind": "call", "outcome": "connected", "lead": lead.pk},
                             HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(Activity.objects.filter(lead=lead, kind="call").count(), 1)


class LeadPageLayoutTests(CrmBase):
    def test_three_cards_compact_buttons_and_working_post(self):
        lead = Lead.objects.create(name="Zed", phone="9876543210", assigned_to=self.a)
        self.login(self.a)
        html = self.client.get(reverse("control:crm_lead", kwargs={"pk": lead.pk})).content.decode()
        for heading in ("Log this contact", ">History<", "Edit details"):
            self.assertIn(heading, html)
        self.assertIn("xl:grid-cols-3", html)
        self.assertIn("rounded-lg px-2.5 py-1.5 text-xs", html)   # compact outcome buttons
        self.assertNotIn("py-3 text-sm font-medium transition", html.split("Log this contact")[1])
        self.assertNotIn("Edit details</summary>", html)  # the edit form is always open, never collapsed
        r = self.client.post(reverse("control:crm_log"), {"kind": "call", "outcome": "connected", "lead": lead.pk})
        self.assertEqual(r.status_code, 302)


class SampleCsvTests(CrmBase):
    def test_sample_downloads_and_round_trips_through_import(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        url = reverse("control:crm_lead_import_sample")
        self.login(self.admin)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r["Content-Type"])
        self.assertIn("leads-sample.csv", r["Content-Disposition"])
        body = r.content
        self.assertTrue(body.startswith("﻿".encode()))  # Excel-safe BOM
        self.assertIn(b"name,phone,business,city,source", body)
        # import the untouched sample: header skipped, 3 leads created, assigned
        up = SimpleUploadedFile("leads-sample.csv", body, content_type="text/csv")
        self.client.post(reverse("control:crm_lead_import"), {"file": up, "assignee": self.a.pk})
        self.assertEqual(Lead.objects.count(), 3)
        self.assertFalse(Lead.objects.filter(name="name").exists())
        self.assertEqual(Lead.objects.get(name="Anita Rao").assigned_to, self.a)
        # importing it again creates no duplicates (phone match)
        up = SimpleUploadedFile("leads-sample.csv", body, content_type="text/csv")
        self.client.post(reverse("control:crm_lead_import"), {"file": up})
        self.assertEqual(Lead.objects.count(), 3)

    def test_sample_and_import_box_visible_to_dgc_and_admin(self):
        url = reverse("control:crm_lead_import_sample")
        for who in (self.a, self.admin):
            self.login(who)
            self.assertEqual(self.client.get(url).status_code, 200)
            html = self.client.get(reverse("control:crm_leads") + "?view=list").content.decode()
            self.assertIn(url, html)
            self.assertIn("Download sample CSV", html)
            self.assertIn('id="import"', html)
            board = self.client.get(reverse("control:crm_lead_board")).content.decode()
            self.assertIn("#import", board)
        # a store owner (not platform staff) still has no access
        self.login(self.owner, store=True)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_assignee_picker_admin_only_in_import_box(self):
        self.login(self.a)
        box = self.client.get(reverse("control:crm_leads") + "?view=list").content.decode().split('id="import"')[1]
        self.assertNotIn('name="assignee"', box.split("</form>")[0])
        self.assertIn("added to your own list", box)
        self.login(self.admin)
        box = self.client.get(reverse("control:crm_leads") + "?view=list").content.decode().split('id="import"')[1]
        self.assertIn('name="assignee"', box.split("</form>")[0])

    def test_dgc_import_lands_on_own_list_even_if_assignee_forged(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        csv_bytes = b"name,phone,business,city,source\nAsha,9000000001,Asha Store,Pune,Fair\nBhanu,9000000002,,,\n"
        self.login(self.a)
        r = self.client.post(reverse("control:crm_lead_import"), {
            "file": SimpleUploadedFile("l.csv", csv_bytes, content_type="text/csv"),
            "assignee": self.b.pk})  # forged: try to push leads onto another DGC
        self.assertEqual(r.status_code, 302)
        leads = Lead.objects.all()
        self.assertEqual(leads.count(), 2)
        self.assertTrue(all(l.assigned_to == self.a and l.created_by == self.a for l in leads))
        # and they show up on that DGC's board, not the other's
        self.assertContains(self.client.get(reverse("control:crm_lead_board")), "Asha")
        self.login(self.b)
        self.assertNotContains(self.client.get(reverse("control:crm_lead_board")), "Asha")

    def test_owner_cannot_import(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.login(self.owner, store=True)
        r = self.client.post(reverse("control:crm_lead_import"), {
            "file": SimpleUploadedFile("l.csv", b"x,1\n", content_type="text/csv")})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Lead.objects.exists())

    def test_admin_import_can_still_pick_assignee(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.login(self.admin)
        self.client.post(reverse("control:crm_lead_import"), {
            "file": SimpleUploadedFile("l.csv", b"Zed,9111111111\n", content_type="text/csv"),
            "assignee": self.b.pk})
        self.assertEqual(Lead.objects.get(name="Zed").assigned_to, self.b)


class CompactToolbarTests(CrmBase):
    def test_board_and_list_toolbars_use_small_controls(self):
        self.login(self.admin)
        for url in (reverse("control:crm_lead_board"), reverse("control:crm_leads") + "?view=list"):
            html = self.client.get(url).content.decode()
            bar = html.split('leading-none">')[1].split("</form>")[0]
            self.assertIn("h-7", bar)
            self.assertNotIn("text-[13px]", bar)
            self.assertNotIn("py-1.5", bar)
            self.assertIn("Unassigned", bar)


class InlineStatusListTests(CrmBase):
    def _list(self, who):
        self.login(who)
        return self.client.get(reverse("control:crm_leads") + "?view=list").content.decode()

    def test_status_select_rendered_without_won_and_won_row_locked(self):
        live = Lead.objects.create(name="Livelead", assigned_to=self.a, status=LeadStatus.INTERESTED)
        won = Lead.objects.create(name="Wonlead", assigned_to=self.a, status=LeadStatus.WON)
        html = self._list(self.a)
        row = html.split(f'data-lead="{live.pk}"')[1].split("</tr>")[0]
        self.assertIn(reverse("control:crm_lead_move", kwargs={"pk": live.pk}), row)
        self.assertIn('value="interested" selected', row)
        self.assertIn('value="lost"', row)
        self.assertNotIn('value="won"', row)
        won_row = html.split("Wonlead")[1].split("</tr>")[0]
        self.assertNotIn("<select", won_row)
        self.assertIn("Won", won_row)

    def test_script_injected_once(self):
        Lead.objects.create(name="L", assigned_to=self.a)
        self.assertEqual(self._list(self.a).count("select[data-lead]"), 1)

    def test_form_carries_csrf_token_for_inline_edit(self):
        from django.test import Client
        Lead.objects.create(name="L", assigned_to=self.a)
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.a)
        html = c.get(reverse("control:crm_leads") + "?view=list").content.decode()
        pre = html.split("data-lead=")[0]
        self.assertIn('name="csrfmiddlewaretoken"', pre[pre.rindex("<form"):])

    def test_inline_change_persists_via_move_endpoint_and_logs(self):
        lead = Lead.objects.create(name="L", assigned_to=self.a)
        self.login(self.a)
        r = self.client.post(reverse("control:crm_lead_move", kwargs={"pk": lead.pk}), {"status": "lost"})
        self.assertEqual(r.status_code, 204)
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.LOST)
        self.assertTrue(Activity.objects.filter(lead=lead, kind=ActivityKind.STAGE).exists())
        # and back to an open stage
        self.client.post(reverse("control:crm_lead_move", kwargs={"pk": lead.pk}), {"status": "contacted"})
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.CONTACTED)


class ArchiveLeadsTests(CrmBase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.url = reverse("control:crm_lead_archive")

    def _lead(self, name, who=None, **kw):
        return Lead.objects.create(name=name, assigned_to=who or self.a, **kw)

    def test_bulk_archive_hides_from_list_board_queue_counts(self):
        keep = self._lead("Keepme")
        gone = self._lead("Archiveme", next_follow_up=self.today - dt.timedelta(days=2))
        self.login(self.a)
        r = self.client.post(self.url, {"ids": [gone.pk], "action": "archive"})
        self.assertEqual(r.status_code, 302)
        gone.refresh_from_db()
        self.assertTrue(gone.is_archived)
        self.assertEqual(gone.archived_by, self.a)
        self.assertIsNotNone(gone.archived_at)
        lst = self.client.get(reverse("control:crm_leads") + "?view=list").content.decode()
        self.assertIn("Keepme", lst)
        self.assertNotIn("Archiveme", lst)
        board = self.client.get(reverse("control:crm_lead_board")).content.decode()
        self.assertNotIn("Archiveme", board)
        self.assertEqual(list(svc.call_queue(self.a)), [keep])
        self.assertEqual({c["status"]: c["count"] for c in svc.board_columns(self.a)["columns"]}["new"], 1)
        self.assertEqual(svc.board_extras()["overdue_follow_ups"], 0)
        self.assertNotContains(self.client.get(reverse("control:crm_my_day")), "Archiveme")

    def test_archived_filter_shows_them_and_restore_brings_back(self):
        lead = self._lead("Archiveme")
        self.login(self.a)
        self.client.post(self.url, {"ids": [lead.pk], "action": "archive"})
        r = self.client.get(reverse("control:crm_leads") + "?archived=1")
        self.assertContains(r, "Archiveme")
        self.assertContains(r, "Restore")
        self.client.post(self.url, {"ids": [lead.pk], "action": "restore"})
        lead.refresh_from_db()
        self.assertFalse(lead.is_archived)
        self.assertIsNone(lead.archived_at)
        self.assertContains(self.client.get(reverse("control:crm_lead_board")), "Archiveme")

    def test_dgc_cannot_archive_someone_elses_lead(self):
        mine, theirs = self._lead("Mine"), self._lead("Theirs", who=self.b)
        self.login(self.a)
        self.client.post(self.url, {"ids": [mine.pk, theirs.pk], "action": "archive"})
        mine.refresh_from_db(); theirs.refresh_from_db()
        self.assertTrue(mine.is_archived)
        self.assertFalse(theirs.is_archived)

    def test_admin_can_archive_any(self):
        theirs = self._lead("Theirs", who=self.b)
        self.login(self.admin)
        self.client.post(self.url, {"ids": [theirs.pk], "action": "archive"})
        theirs.refresh_from_db()
        self.assertTrue(theirs.is_archived)

    def test_limits_and_empty_selection(self):
        lead = self._lead("L")
        self.login(self.a)
        self.client.post(self.url, {"action": "archive"})
        self.client.post(self.url, {"ids": list(range(1, svc.BULK_ARCHIVE_MAX + 2)), "action": "archive"})
        lead.refresh_from_db()
        self.assertFalse(lead.is_archived)

    def test_owner_blocked_and_csrf_enforced(self):
        from django.test import Client
        lead = self._lead("L")
        self.login(self.owner, store=True)
        self.assertEqual(self.client.post(self.url, {"ids": [lead.pk], "action": "archive"}).status_code, 403)
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.a)
        self.assertEqual(c.post(self.url, {"ids": [lead.pk], "action": "archive"}).status_code, 403)

    def test_htmx_style_call_returns_204(self):
        lead = self._lead("L")
        self.login(self.a)
        r = self.client.post(self.url, {"ids": [lead.pk], "action": "archive"}, HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 204)

    def test_open_redirect_guard(self):
        lead = self._lead("L")
        self.login(self.a)
        r = self.client.post(self.url, {"ids": [lead.pk], "action": "archive", "next": "https://evil.test/"})
        self.assertNotIn("evil.test", r["Location"])

    def test_list_has_bulk_checkboxes_and_archive_button_for_dgc_and_admin(self):
        self._lead("L")
        for who in (self.a, self.admin):
            self.login(who)
            html = self.client.get(reverse("control:crm_leads") + "?view=list").content.decode()
            self.assertIn('name="ids"', html)
            self.assertIn('value="archive"', html)
            self.assertIn("🗄 Archived", html)
        self.login(self.a)  # DGC has no assign controls
        html = self.client.get(reverse("control:crm_leads") + "?view=list").content.decode()
        self.assertNotIn("give to", html)
        self.login(self.admin)
        self.assertIn("give to", self.client.get(reverse("control:crm_leads") + "?view=list").content.decode())

    def test_archived_view_offers_restore_not_archive(self):
        lead = self._lead("L")
        svc.archive_leads(Lead.objects.filter(pk=lead.pk), actor=self.a)
        self.login(self.a)
        html = self.client.get(reverse("control:crm_leads") + "?archived=1").content.decode()
        self.assertIn('value="restore"', html)
        self.assertNotIn('value="archive"', html)

    def test_lead_page_shows_archive_then_restore(self):
        lead = self._lead("L")
        self.login(self.a)
        url = reverse("control:crm_lead", kwargs={"pk": lead.pk})
        self.assertIn('value="archive"', self.client.get(url).content.decode())
        self.client.post(self.url, {"ids": [lead.pk], "action": "archive"})
        html = self.client.get(url).content.decode()   # archived lead still reachable by owner
        self.assertIn("This lead is archived", html)
        self.assertIn('value="restore"', html)

    def test_board_menu_has_archive_and_script_syntax_hooks(self):
        self._lead("L")
        self.login(self.a)
        html = self.client.get(reverse("control:crm_lead_board")).content.decode()
        self.assertIn("archive(", html)
        self.assertIn("data-archive-url", html)

    def test_import_dedupe_still_sees_archived_phone(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        lead = self._lead("Old", phone="9000000009")
        svc.archive_leads(Lead.objects.filter(pk=lead.pk), actor=self.a)
        self.login(self.a)
        self.client.post(reverse("control:crm_lead_import"),
                         {"file": SimpleUploadedFile("l.csv", b"Dup,9000000009\n", content_type="text/csv")})
        self.assertEqual(Lead.objects.count(), 1)

    def test_archive_writes_audit(self):
        from apps.core.models import AuditLog
        lead = self._lead("L")
        svc.archive_leads(Lead.objects.filter(pk=lead.pk), actor=self.a)
        self.assertTrue(AuditLog.objects.filter(actor=self.a, changes__crm_leads_archived=1).exists())


class TrainingFlowTests(CrmBase):
    def setUp(self):
        super().setUp()
        self.c = _dgc("chetan")
        self.url = reverse("control:crm_training_create")

    def _post(self, who, **data):
        self.login(who)
        return self.client.post(self.url, data)

    def _resp(self, log, who, action="confirm"):
        self.login(who)
        return self.client.post(reverse("control:crm_training_respond", kwargs={"pk": log.pk}), {"action": action})

    # ---- "I trained someone"
    def test_trainer_adds_student_account_student_confirms(self):
        self._post(self.a, mode="trained_student", student=self.b.pk, topic="Setup")
        t = TrainingLog.objects.get()
        self.assertEqual((t.trainer, t.trainee, t.initiated_by, t.status), (self.a, self.b, self.a, "pending"))
        # the trainer who created it cannot confirm their own claim
        self.assertEqual(self._resp(t, self.a).status_code, 403)
        # a third DGC cannot either
        self.assertEqual(self._resp(t, self.c).status_code, 403)
        self.assertEqual(self._resp(t, self.b).status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.status, TrainingStatus.CONFIRMED)

    def test_name_only_student_needs_admin_to_confirm(self):
        self._post(self.a, mode="trained_student", student="__new", student_name="Ravi Kumar", student_phone="9000000000")
        t = TrainingLog.objects.get()
        self.assertIsNone(t.trainee)
        self.assertEqual((t.student_name, t.student_phone, t.status), ("Ravi Kumar", "9000000000", "pending"))
        self.assertEqual(t.student_label, "Ravi Kumar")
        self.assertEqual(self._resp(t, self.a).status_code, 403)   # can't self-verify
        self.assertEqual(self._resp(t, self.b).status_code, 403)
        self.assertEqual(self._resp(t, self.admin).status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.status, TrainingStatus.CONFIRMED)

    def test_student_validation(self):
        self._post(self.a, mode="trained_student", student=self.a.pk)            # self
        self._post(self.a, mode="trained_student", student="__new", student_name="  ")  # no name
        self._post(self.a, mode="trained_student")                               # nothing
        self.assertFalse(TrainingLog.objects.exists())

    # ---- "I was trained by"  (original flow, unchanged)
    def test_trained_by_flow_trainer_confirms(self):
        self._post(self.b, mode="trained_by", trainer=self.a.pk)
        t = TrainingLog.objects.get()
        self.assertEqual((t.trainee, t.trainer, t.initiated_by), (self.b, self.a, self.b))
        self.assertEqual(self._resp(t, self.b).status_code, 403)
        self.assertEqual(self._resp(t, self.a).status_code, 302)

    def test_cannot_name_self_as_trainer(self):
        self._post(self.b, mode="trained_by", trainer=self.b.pk)
        self.assertFalse(TrainingLog.objects.exists())

    # ---- request -> assign -> trained -> confirm
    def test_full_request_assign_train_confirm_cycle(self):
        self._post(self.b, mode="request", topic="Demo script")        # for myself
        t = TrainingLog.objects.get()
        self.assertEqual((t.trainee, t.trainer, t.status), (self.b, None, TrainingStatus.REQUESTED))
        # counts nowhere yet
        self.assertEqual(svc.person_numbers(timezone.localdate())[1]["trained"], 0)
        # only the super admin assigns; a DGC cannot, not even self-claim
        self.login(self.a)
        aurl = reverse("control:crm_training_assign", kwargs={"pk": t.pk})
        self.assertEqual(self.client.post(aurl, {"trainer": self.a.pk}).status_code, 403)
        self.login(self.b)
        self.assertEqual(self.client.post(aurl, {"trainer": self.b.pk}).status_code, 403)
        t.refresh_from_db()
        self.assertIsNone(t.trainer)
        self.login(self.admin)
        self.client.post(aurl, {"trainer": self.a.pk})
        t.refresh_from_db()
        self.assertEqual((t.status, t.trainer, t.assigned_by), (TrainingStatus.ASSIGNED, self.a, self.admin))
        # only the assigned trainer marks it trained
        murl = reverse("control:crm_training_trained", kwargs={"pk": t.pk})
        self.login(self.c)
        self.assertEqual(self.client.post(murl).status_code, 403)
        self.login(self.a)
        self.assertEqual(self.client.post(murl).status_code, 302)
        t.refresh_from_db()
        self.assertEqual((t.status, t.initiated_by), (TrainingStatus.PENDING, self.a))
        # the student confirms; the trainer cannot confirm their own claim
        self.assertEqual(self._resp(t, self.a).status_code, 403)
        self.assertEqual(self._resp(t, self.b).status_code, 302)
        rows, totals = svc.person_numbers(timezone.localdate())
        by = {r["user"].pk: r for r in rows}
        self.assertEqual((totals["trained"], by[self.b.pk]["trained"], by[self.a.pk]["trained_others"]), (1, 1, 1))

    def test_admin_can_assign_and_reassign_but_not_self_train(self):
        t = TrainingLog.objects.create(trainee=self.b, status=TrainingStatus.REQUESTED, initiated_by=self.b)
        self.login(self.admin)
        aurl = reverse("control:crm_training_assign", kwargs={"pk": t.pk})
        self.client.post(aurl, {"trainer": self.b.pk})            # trainee as own trainer -> refused
        t.refresh_from_db()
        self.assertIsNone(t.trainer)
        self.client.post(aurl, {"trainer": self.a.pk})
        self.client.post(aurl, {"trainer": self.c.pk})            # reassign
        t.refresh_from_db()
        self.assertEqual((t.trainer, t.status), (self.c, TrainingStatus.ASSIGNED))

    def test_admin_marking_trained_confirms_directly(self):
        t = TrainingLog.objects.create(trainee=self.b, trainer=self.a, status=TrainingStatus.ASSIGNED, initiated_by=self.b)
        self.login(self.admin)
        self.client.post(reverse("control:crm_training_trained", kwargs={"pk": t.pk}))
        t.refresh_from_db()
        self.assertEqual(t.status, TrainingStatus.CONFIRMED)
        self.assertIsNotNone(t.confirmed_at)

    def test_request_for_someone_new_and_admin_on_behalf(self):
        self._post(self.a, mode="request", for_user="__new", student_name="New Recruit", student_phone="9111111111")
        t = TrainingLog.objects.get()
        self.assertEqual((t.trainee, t.student_name, t.initiated_by), (None, "New Recruit", self.a))
        self._post(self.a, mode="request", for_user="__new")                      # nameless -> refused
        self.assertEqual(TrainingLog.objects.count(), 1)
        self._post(self.a, mode="request", for_user=self.b.pk)                    # DGC can't file for others
        self.assertEqual(TrainingLog.objects.count(), 1)
        self._post(self.admin, mode="request", for_user=self.b.pk)               # admin can
        self.assertEqual(TrainingLog.objects.count(), 2)
        self.assertEqual(TrainingLog.objects.latest("pk").trainee, self.b)

    def test_cancel_request_by_requester_or_admin_only(self):
        t = TrainingLog.objects.create(trainee=self.b, status=TrainingStatus.REQUESTED, initiated_by=self.b)
        curl = reverse("control:crm_training_cancel", kwargs={"pk": t.pk})
        self.login(self.c)
        self.assertEqual(self.client.post(curl).status_code, 403)
        self.login(self.b)
        self.assertEqual(self.client.post(curl).status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.status, TrainingStatus.REJECTED)

    def test_cannot_assign_or_mark_a_finished_training(self):
        t = TrainingLog.objects.create(trainee=self.b, trainer=self.a, status=TrainingStatus.CONFIRMED, initiated_by=self.b)
        self.login(self.admin)
        self.client.post(reverse("control:crm_training_assign", kwargs={"pk": t.pk}), {"trainer": self.c.pk})
        t.refresh_from_db()
        self.assertEqual(t.trainer, self.a)
        self.login(self.a)
        self.assertEqual(self.client.post(reverse("control:crm_training_trained", kwargs={"pk": t.pk})).status_code, 403)

    # ---- visibility + counting
    def test_dgc_sees_only_own_trainings(self):
        TrainingLog.objects.create(trainee=self.b, trainer=self.a, topic="Mine-ish", initiated_by=self.b)
        TrainingLog.objects.create(trainee=self.c, trainer=self.c, student_name="", topic="Unrelated", initiated_by=self.c)
        self.login(self.b)
        html = self.client.get(reverse("control:crm_training")).content.decode()
        self.assertIn("Mine-ish", html)
        self.assertNotIn("Unrelated", html)
        self.login(self.admin)
        html = self.client.get(reverse("control:crm_training")).content.decode()
        self.assertIn("Mine-ish", html)
        self.assertIn("Unrelated", html)

    def test_page_shows_assign_only_to_admin_and_mark_to_trainer(self):
        TrainingLog.objects.create(trainee=self.b, status=TrainingStatus.REQUESTED, initiated_by=self.b)
        assigned = TrainingLog.objects.create(trainee=self.c, trainer=self.a, status=TrainingStatus.ASSIGNED, initiated_by=self.c)
        self.login(self.admin)
        html = self.client.get(reverse("control:crm_training")).content.decode()
        self.assertIn("/assign/", html)
        self.login(self.b)
        html = self.client.get(reverse("control:crm_training")).content.decode()
        self.assertNotIn("/assign/", html)
        self.login(self.a)
        html = self.client.get(reverse("control:crm_training")).content.decode()
        self.assertIn("Mark trained", html)
        self.assertIn("waiting for you to train", html)
        self.login(self.c)
        self.assertNotIn("Mark trained", self.client.get(reverse("control:crm_training")).content.decode())

    def test_board_counts_name_only_student_and_shows_request_chip(self):
        TrainingLog.objects.create(trainer=self.a, student_name="Walk In", status=TrainingStatus.CONFIRMED, initiated_by=self.a)
        TrainingLog.objects.create(trainee=self.b, status=TrainingStatus.REQUESTED, initiated_by=self.b)
        self.assertEqual(svc.person_numbers(timezone.localdate())[1]["trained"], 1)
        self.assertEqual(svc.board_extras()["training_requests"], 1)
        self.login(self.admin)
        self.assertContains(self.client.get(reverse("control:crm_board")), "Assign a trainer")

    def test_my_day_shows_confirm_and_waiting_cards(self):
        TrainingLog.objects.create(trainee=self.b, trainer=self.a, status=TrainingStatus.PENDING, initiated_by=self.a)
        TrainingLog.objects.create(trainee=self.c, trainer=self.b, status=TrainingStatus.ASSIGNED, initiated_by=self.c)
        self.login(self.b)
        html = self.client.get(reverse("control:crm_my_day")).content.decode()
        self.assertIn("1 training to confirm", html)
        self.assertIn("waiting for you to train", html)

    def test_owner_blocked_and_csrf(self):
        from django.test import Client
        self.login(self.owner, store=True)
        self.assertEqual(self.client.post(self.url, {"mode": "request"}).status_code, 403)
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.a)
        self.assertEqual(c.post(self.url, {"mode": "request"}).status_code, 403)

    def test_form_has_three_modes_and_token(self):
        self.login(self.a)
        html = self.client.get(reverse("control:crm_training")).content.decode()
        for needle in ("I trained someone", "I was trained by", "Wants training", 'name="mode"',
                       "__new", 'name="csrfmiddlewaretoken"'):
            self.assertIn(needle, html)

    def test_migration_backfill_marks_old_rows_trainee_initiated(self):
        # rows created through the old flow keep working: trainer confirms, trainee cannot
        t = TrainingLog.objects.create(trainee=self.b, trainer=self.a, initiated_by=self.b)
        self.assertTrue(svc.can_confirm_training(t, self.a))
        self.assertFalse(svc.can_confirm_training(t, self.b))


class LeadLogStepsTests(CrmBase):
    def setUp(self):
        super().setUp()
        self.lead = Lead.objects.create(name="Zed", phone="9213529044", assigned_to=self.a)
        self.login(self.a)
        self.page = self.client.get(reverse("control:crm_lead", kwargs={"pk": self.lead.pk})).content.decode()
        self.form = self.page.split("What happened?")[0].rsplit("<form", 1)[1] + "What happened?" + \
            self.page.split("What happened?")[1].split("</form>")[0]

    def test_three_labelled_steps_and_one_save(self):
        for needle in ("What happened?", "Remind me to follow up", "Save", "Phone call", "Something else"):
            self.assertIn(needle, self.form)
        # nothing on this page saves on a single outcome tap any more
        self.assertNotIn('type="submit" name="outcome"', self.page)
        self.assertEqual(self.form.count("<button"), 1)           # exactly one submit button

    def test_every_choice_has_a_hint_and_a_label(self):
        import re
        choices = re.findall(r'<input type="radio" value="([a-z_]+\|[a-z_]+)"', self.form)
        self.assertEqual(len(choices), 7)
        for c in choices:
            self.assertGreaterEqual(self.form.count(f"'{c}'"), 2, c)  # hints + labels maps

    def test_follow_up_options_default_to_automatic(self):
        self.assertIn('name="follow_in" value="" x-model="follow" class="peer sr-only" checked', self.form)
        for lab in ("Automatic", "Tomorrow", "In 3 days", "Next week"):
            self.assertIn(lab, self.form)

    def test_form_posts_what_server_expects(self):
        url = reverse("control:crm_log")
        base = {"lead": self.lead.pk, "next": reverse("control:crm_lead", kwargs={"pk": self.lead.pk})}
        r = self.client.post(url, {**base, "kind": "call", "outcome": "no_answer", "follow_in": "", "note": "voicemail"})
        self.assertEqual(r.status_code, 302)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.next_follow_up, timezone.localdate() + dt.timedelta(days=1))  # "Automatic"
        act = Activity.objects.get(lead=self.lead)
        self.assertEqual((act.kind, act.outcome, act.note), ("call", "no_answer", "voicemail"))
        self.client.post(url, {**base, "kind": "call", "outcome": "connected", "follow_in": "7"})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.next_follow_up, timezone.localdate() + dt.timedelta(days=7))
        self.client.post(url, {**base, "kind": "demo", "outcome": "done", "follow_in": ""})
        self.assertTrue(Activity.objects.filter(lead=self.lead, kind="demo").exists())
        self.client.post(url, {**base, "kind": "whatsapp", "outcome": "done", "follow_in": ""})
        self.assertTrue(Activity.objects.filter(lead=self.lead, kind="whatsapp").exists())

    def test_not_interested_closes_lead(self):
        self.client.post(reverse("control:crm_log"), {"lead": self.lead.pk, "kind": "call", "outcome": "not_interested"})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, LeadStatus.LOST)

    def test_saving_without_a_choice_is_a_friendly_error_not_a_404(self):
        r = self.client.post(reverse("control:crm_log"), {"lead": self.lead.pk, "kind": "", "outcome": "",
                                                          "next": reverse("control:crm_lead", kwargs={"pk": self.lead.pk})})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Activity.objects.exists())
        self.assertEqual(self.client.post(reverse("control:crm_log"), {"kind": "bogus"}).status_code, 404)

    def test_my_day_keeps_one_tap_buttons(self):
        html = self.client.get(reverse("control:crm_my_day")).content.decode()
        self.assertIn('type="submit" name="outcome"', html)

    def test_page_still_carries_csrf_and_call_buttons(self):
        self.assertIn('name="csrfmiddlewaretoken"', self.form)
        self.assertIn("tel:9213529044", self.page)
        self.assertIn("wa.me/919213529044", self.page)


class MyDayCompactTests(CrmBase):
    def setUp(self):
        super().setUp()
        self.login(self.a)
        self.url = reverse("control:crm_my_day")

    def _html(self):
        return self.client.get(self.url).content.decode()

    def test_followup_and_note_come_before_the_result_buttons(self):
        Lead.objects.create(name="Alpha Lead", phone="9876543210", assigned_to=self.a)
        html = self._html()
        form = html.split("Next up")[1]
        self.assertLess(form.index('name="follow_in"'), form.index('type="submit" name="outcome"'))
        self.assertLess(form.index('name="note"'), form.index('type="submit" name="outcome"'))
        self.assertIn("Tap the result", form)
        self.assertEqual(form.count('type="submit" name="outcome"'), 5)   # 5 call results
        self.assertIn('name="kind" value="demo"', form)                    # + demo

    def test_compact_markers_and_slim_stats_strip(self):
        Lead.objects.create(name="Alpha Lead", assigned_to=self.a)
        html = self._html()
        self.assertIn("divide-x", html)                  # one strip, not four tall tiles
        self.assertIn("Calls today", html)
        self.assertNotIn("py-3 text-sm font-medium transition", html)   # old big result buttons gone
        strip = html.split("Calls today")[0][-400:] + html.split("Calls today")[1].split("Next up")[0]
        self.assertNotIn("text-2xl", strip)              # stat numbers are text-lg now

    def test_one_tap_result_with_note_and_followup_posts(self):
        lead = Lead.objects.create(name="Alpha Lead", assigned_to=self.a)
        r = self.client.post(reverse("control:crm_log"), {
            "lead": lead.pk, "kind": "call", "outcome": "no_answer", "follow_in": "3",
            "note": "try evening", "next": self.url})
        self.assertRedirects(r, self.url, fetch_redirect_response=False)
        lead.refresh_from_db()
        self.assertEqual(lead.next_follow_up, timezone.localdate() + dt.timedelta(days=3))
        self.assertEqual(Activity.objects.get(lead=lead).note, "try evening")

    def test_lists_cap_at_six_with_see_all_links(self):
        for i in range(8):
            Lead.objects.create(name=f"Due{i}", assigned_to=self.a, status=LeadStatus.CONTACTED,
                                next_follow_up=timezone.localdate() - dt.timedelta(days=1))
        for i in range(8):
            Task.objects.create(title=f"Task{i}", assignee=self.a)
        html = self._html()
        due_box = html.split("Follow-ups due")[1].split("My tasks")[0]
        task_box = html.split("My tasks")[1]
        self.assertEqual(sum(f"Due{i}" in due_box for i in range(8)), 6)      # list capped at 6
        self.assertEqual(sum(f"Task{i}" in task_box for i in range(8)), 6)
        self.assertIn("view=list&due=1", due_box)                             # "all →" link
        self.assertIn(reverse("control:crm_tasks"), task_box)
        self.assertRegex(due_box, r">\s*8\s*<")                              # badge shows the true total
        self.assertRegex(task_box.split("</h3>")[0], r">\s*8\s*<")

    def test_empty_queue_is_a_slim_banner_with_a_next_step(self):
        html = self._html()
        self.assertIn("Queue clear", html)
        self.assertIn(reverse("control:crm_leads"), html)

    def test_alert_chips_only_when_something_waits(self):
        self.assertNotIn("to confirm", self._html())
        TrainingLog.objects.create(trainee=self.a, trainer=self.b, status=TrainingStatus.PENDING, initiated_by=self.b)
        self.assertIn("1 training to confirm", self._html())

    def test_store_logger_only_for_assigned_dgcs(self):
        self.assertNotIn("products to", self._html())
        wr = StoreWorkRequest.objects.create(project=self.store, kind="catalog", requested_by=self.owner)
        svc.assign_store_work(wr, self.a, actor=self.admin)
        html = self._html()
        self.assertIn("products to", html)
        self.assertIn("ShopCo", html)

    def test_range_tabs_still_work(self):
        for key in ("today", "yesterday", "7d", "month"):
            self.assertEqual(self.client.get(self.url + f"?range={key}").status_code, 200)

    def test_csrf_present_on_the_logging_form(self):
        from django.test import Client
        Lead.objects.create(name="Alpha Lead", assigned_to=self.a)
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.a)
        html = c.get(self.url).content.decode()
        form = html.split('action="%s"' % reverse("control:crm_log"))[1].split("</form>")[0]
        self.assertIn('name="csrfmiddlewaretoken"', form)


class BoardCompactTests(CrmBase):
    def setUp(self):
        super().setUp()
        svc.log_activity(actor=self.a, kind="call", outcome="connected")
        svc.log_activity(actor=self.a, kind="demo", outcome="done")
        self.login(self.admin)
        self.html = self.client.get(reverse("control:crm_board")).content.decode()

    def test_one_slim_stat_strip_with_all_six_numbers(self):
        self.assertIn("divide-x", self.html)
        for label in ("Calls", "Demos", "Trained", "Collection", "Products", "Won today"):
            self.assertIn(f">{label}<", self.html)
        self.assertIn("1 spoke · 100%", self.html)
        strip = self.html.split("divide-x")[1].split("</table>")[0]
        self.assertNotIn("text-2xl", strip)          # numbers are text-lg now
        self.assertNotIn("p-4 shadow-sm", strip.split("<table")[0])   # no tall tiles

    def test_table_rows_are_dense(self):
        table = self.html.split("<table")[1].split("</table>")[0]
        self.assertNotIn("py-3", table)
        self.assertIn("py-2", table)
        self.assertIn("text-[13px]", table)

    def test_inbox_only_appears_when_something_waits_and_stays_compact(self):
        self.assertNotIn("Assign store work", self.html)
        self.assertNotIn("Verify collections", self.html)
        StoreWorkRequest.objects.create(project=self.store, kind="catalog", requested_by=self.a)
        TrainingLog.objects.create(trainee=self.b, status=TrainingStatus.REQUESTED, initiated_by=self.b)
        html = self.client.get(reverse("control:crm_board")).content.decode()
        self.assertIn("Assign store work", html)
        self.assertIn("Assign a trainer", html)
        panel = html.split("Assign store work")[1].split("</form>")[0]
        self.assertIn("h-8", panel)                       # compact controls, not tall inputs

    def test_all_ranges_and_idle_toggle_still_work(self):
        for q in ("?range=yesterday", "?range=7d", "?range=month&active=0", "?active=1"):
            self.assertEqual(self.client.get(reverse("control:crm_board") + q).status_code, 200)
        self.assertIn("Show everyone", self.html)


class MobileEasyTests(CrmBase):
    """Status everywhere, call -> return -> one tap, quick add, bulk status."""

    def setUp(self):
        super().setUp()
        self.lead = Lead.objects.create(name="Zed", phone="9213529044", assigned_to=self.a,
                                        status=LeadStatus.CONTACTED)
        self.login(self.a)

    def _get(self, name, **kw):
        return self.client.get(reverse(f"control:{name}", kwargs=kw)).content.decode()

    # ---- one status dropdown, every screen
    def test_status_dropdown_on_list_my_day_and_lead_page(self):
        lead_url = reverse("control:crm_lead_move", kwargs={"pk": self.lead.pk})
        pages = {
            "list": self.client.get(reverse("control:crm_leads") + "?view=list").content.decode(),
            "my_day": self._get("crm_my_day"),
            "lead": self._get("crm_lead", pk=self.lead.pk),
        }
        for name, html in pages.items():
            self.assertIn(f'data-lead="{self.lead.pk}"', html, name)
            self.assertIn(lead_url, html, name)
            self.assertIn('value="contacted" selected', html, name)
            self.assertNotIn('value="won"', html.split(f'data-lead="{self.lead.pk}"')[1].split("</select>")[0], name)

    def test_dropdown_options_match_lead_status_enum(self):
        import re
        html = self._get("crm_lead", pk=self.lead.pk)
        sel = html.split(f'data-lead="{self.lead.pk}"')[1].split("</select>")[0]
        offered = set(re.findall(r'<option value="([a-z_]+)"', sel))
        self.assertEqual(offered, {v for v in LeadStatus.values if v != "won"})

    def test_won_lead_shows_locked_badge_not_a_dropdown(self):
        Lead.objects.filter(pk=self.lead.pk).update(status=LeadStatus.WON)
        html = self._get("crm_lead", pk=self.lead.pk)
        self.assertNotIn(f'data-lead="{self.lead.pk}"', html)
        self.assertIn("Won", html)

    def test_dropdown_saves_through_move_endpoint(self):
        r = self.client.post(reverse("control:crm_lead_move", kwargs={"pk": self.lead.pk}), {"status": "interested"})
        self.assertEqual(r.status_code, 204)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, LeadStatus.INTERESTED)

    def test_shared_status_script_lives_once_in_the_shell(self):
        for html in (self._get("crm_my_day"), self.client.get(reverse("control:crm_leads") + "?view=list").content.decode(),
                     self._get("crm_lead", pk=self.lead.pk)):
            self.assertEqual(html.count("select[data-lead]"), 1)
            self.assertIn("window.__crmToast", html)

    # ---- call -> return -> one tap
    def test_call_links_are_tagged_everywhere(self):
        tagged = f'data-call-lead="{self.lead.pk}"'
        self.assertIn(tagged, self._get("crm_my_day"))
        self.assertIn(tagged, self._get("crm_lead", pk=self.lead.pk))
        self.assertIn(tagged, self.client.get(reverse("control:crm_leads") + "?view=list").content.decode())
        self.assertIn(tagged, self.client.get(reverse("control:crm_lead_board")).content.decode())

    def test_call_return_sheet_present_with_log_url_and_all_outcomes(self):
        html = self._get("crm_my_day")
        sheet = html.split('x-data="callSheet()"')[1].split("</script>")[0]
        self.assertIn(reverse("control:crm_log"), sheet)
        for outcome in ("connected", "no_answer", "not_reachable", "call_back", "not_interested"):
            self.assertIn(f"log('{outcome}')", sheet)
        self.assertIn("How did the call with", sheet)
        self.assertIn("env(safe-area-inset-bottom)", sheet)     # iPhone home-bar safe

    def test_sheet_post_shape_is_accepted_by_the_log_endpoint(self):
        r = self.client.post(reverse("control:crm_log"), {"kind": "call", "outcome": "no_answer", "lead": self.lead.pk},
                             HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 204)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.next_follow_up, timezone.localdate() + dt.timedelta(days=1))

    # ---- quick add
    def test_quick_add_with_phone_only_uses_number_as_name(self):
        r = self.client.post(reverse("control:crm_lead_create"),
                             {"phone": "9000011111", "name": "", "next": reverse("control:crm_my_day")})
        self.assertRedirects(r, reverse("control:crm_my_day"), fetch_redirect_response=False)
        lead = Lead.objects.get(phone="9000011111")
        self.assertEqual((lead.name, lead.assigned_to, lead.created_by), ("9000011111", self.a, self.a))

    def test_quick_add_duplicate_phone_is_refused_without_leaking_owner(self):
        Lead.objects.create(name="Other", phone="9000022222", assigned_to=self.b)
        r = self.client.post(reverse("control:crm_lead_create"),
                             {"phone": "9000022222", "next": reverse("control:crm_my_day")}, follow=True)
        self.assertEqual(Lead.objects.filter(phone="9000022222").count(), 1)
        body = r.content.decode()
        self.assertIn("already in the CRM", body)
        self.assertNotIn("bina", body.split("already in the CRM")[1][:200])

    def test_quick_add_needs_name_or_phone(self):
        before = Lead.objects.count()
        self.client.post(reverse("control:crm_lead_create"), {"name": "", "phone": ""})
        self.assertEqual(Lead.objects.count(), before)

    def test_my_day_has_quick_add_and_dismissible_tip(self):
        html = self._get("crm_my_day")
        self.assertIn("Quick add a lead", html)
        self.assertIn('name="phone"', html.split("Quick add a lead")[1])
        self.assertIn("How it works", html)
        self.assertIn("crmTipSeen1", html)

    # ---- bulk status
    def test_bulk_set_status_scoped_and_logged(self):
        mine2 = Lead.objects.create(name="M2", assigned_to=self.a)
        theirs = Lead.objects.create(name="T", assigned_to=self.b)
        won = Lead.objects.create(name="W", assigned_to=self.a, status=LeadStatus.WON)
        url = reverse("control:crm_lead_bulk_status")
        self.client.post(url, {"ids": [self.lead.pk, mine2.pk, theirs.pk, won.pk], "status": "demo_booked"})
        for l in (self.lead, mine2, theirs, won):
            l.refresh_from_db()
        self.assertEqual((self.lead.status, mine2.status), (LeadStatus.DEMO_BOOKED, LeadStatus.DEMO_BOOKED))
        self.assertEqual(theirs.status, LeadStatus.NEW)          # someone else's: untouched
        self.assertEqual(won.status, LeadStatus.WON)             # won stays locked
        self.assertEqual(Activity.objects.filter(kind=ActivityKind.STAGE).count(), 2)

    def test_bulk_status_refuses_won_unknown_empty_and_oversize(self):
        url = reverse("control:crm_lead_bulk_status")
        for data in ({"ids": [self.lead.pk], "status": "won"}, {"ids": [self.lead.pk], "status": "zzz"},
                     {"status": "lost"}, {"ids": list(range(1, svc.BULK_ARCHIVE_MAX + 2)), "status": "lost"}):
            self.client.post(url, data)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, LeadStatus.CONTACTED)

    def test_bulk_status_owner_blocked_csrf_and_redirect_guard(self):
        from django.test import Client
        url = reverse("control:crm_lead_bulk_status")
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.a)
        self.assertEqual(c.post(url, {"ids": [self.lead.pk], "status": "lost"}).status_code, 403)
        r = self.client.post(url, {"ids": [self.lead.pk], "status": "lost", "next": "https://evil.test/"})
        self.assertNotIn("evil.test", r["Location"])
        self.login(self.owner, store=True)
        self.assertEqual(self.client.post(url, {"ids": [self.lead.pk], "status": "lost"}).status_code, 403)

    def test_list_bulk_bar_offers_set_status(self):
        html = self.client.get(reverse("control:crm_leads") + "?view=list").content.decode()
        self.assertIn(reverse("control:crm_lead_bulk_status"), html)
        self.assertIn("set status", html)
        archived = self.client.get(reverse("control:crm_leads") + "?archived=1").content.decode()
        self.assertNotIn("set status", archived)

    # ---- phone-friendly list + lead page
    def test_list_is_simpler_on_phones(self):
        Lead.objects.filter(pk=self.lead.pk).update(next_follow_up=timezone.localdate())
        html = self.client.get(reverse("control:crm_leads") + "?view=list").content.decode()
        self.assertIn('class="hidden px-3 py-2 sm:table-cell">Follow-up', html)   # column hides on phones
        row = html.split('href="/admin/crm/leads/%d/"' % self.lead.pk)[1].split("</tr>")[0]
        self.assertIn("sm:hidden", row)                                           # follow-up shown under the name
        self.assertIn("9213529044", row)
        self.assertIn("py-2 text-sm text-white", row)                             # big call button on phones

    def test_lead_page_stepper_is_desktop_only(self):
        html = self._get("crm_lead", pk=self.lead.pk)
        self.assertIn("hidden gap-1 overflow-x-auto", html)
        self.assertIn("sm:flex", html)


class AdminInboxTests(CrmBase):
    """The super admin clears approvals from the board itself — no page hopping."""

    def setUp(self):
        super().setUp()
        self.board = reverse("control:crm_board")
        self.login(self.admin)

    def _html(self):
        return self.client.get(self.board).content.decode()

    def test_empty_inbox_shows_nothing(self):
        html = self._html()
        for t in ("Verify collections", "Assign store work", "Assign a trainer", "Confirm trainings"):
            self.assertNotIn(t, html)

    def test_verify_collection_from_board_returns_to_board(self):
        c = Collection.objects.create(collected_by=self.a, amount=Decimal("2999"), mode=CollectionMode.UPI, reference="UTR9")
        html = self._html()
        self.assertIn("Verify collections", html)
        self.assertIn("UTR9", html)
        self.assertIn(reverse("control:crm_collection_verify", kwargs={"pk": c.pk}), html)
        r = self.client.post(reverse("control:crm_collection_verify", kwargs={"pk": c.pk}),
                             {"action": "verify", "next": self.board})
        self.assertRedirects(r, self.board, fetch_redirect_response=False)
        c.refresh_from_db()
        self.assertEqual(c.status, CollectionStatus.VERIFIED)
        self.assertNotIn("Verify collections", self._html())          # cleared from the inbox

    def test_reject_collection_from_board(self):
        c = Collection.objects.create(collected_by=self.a, amount=Decimal("10"), mode=CollectionMode.CASH, reference="R")
        self.client.post(reverse("control:crm_collection_verify", kwargs={"pk": c.pk}), {"action": "reject", "next": self.board})
        c.refresh_from_db()
        self.assertEqual(c.status, CollectionStatus.REJECTED)

    def test_assign_store_work_from_board(self):
        wr = StoreWorkRequest.objects.create(project=self.store, kind="catalog", requested_by=self.a, note="40 products")
        html = self._html()
        self.assertIn("40 products", html)
        r = self.client.post(reverse("control:crm_work_assign", kwargs={"pk": wr.pk}), {"assignee": self.b.pk, "next": self.board})
        self.assertRedirects(r, self.board, fetch_redirect_response=False)
        wr.refresh_from_db()
        self.assertEqual((wr.status, wr.assigned_to), (WorkRequestStatus.ASSIGNED, self.b))

    def test_assign_trainer_and_confirm_training_from_board(self):
        req = TrainingLog.objects.create(trainee=self.b, status=TrainingStatus.REQUESTED, initiated_by=self.b, topic="Demo")
        pend = TrainingLog.objects.create(trainer=self.a, student_name="Walk In", status=TrainingStatus.PENDING, initiated_by=self.a)
        html = self._html()
        self.assertIn("Assign a trainer", html)
        self.assertIn("Confirm trainings", html)
        self.assertIn("no login", html)
        r = self.client.post(reverse("control:crm_training_assign", kwargs={"pk": req.pk}), {"trainer": self.a.pk, "next": self.board})
        self.assertRedirects(r, self.board, fetch_redirect_response=False)
        r = self.client.post(reverse("control:crm_training_respond", kwargs={"pk": pend.pk}), {"action": "confirm", "next": self.board})
        self.assertRedirects(r, self.board, fetch_redirect_response=False)
        req.refresh_from_db(); pend.refresh_from_db()
        self.assertEqual((req.status, req.trainer), (TrainingStatus.ASSIGNED, self.a))
        self.assertEqual(pend.status, TrainingStatus.CONFIRMED)

    def test_inbox_caps_rows_and_shows_true_total(self):
        for i in range(8):
            Collection.objects.create(collected_by=self.a, amount=Decimal("1"), mode=CollectionMode.CASH, reference=f"REF{i}")
        html = self._html()
        panel = html.split("Verify collections")[1].split("Assign store work")[0] if "Assign store work" in html else html.split("Verify collections")[1]
        self.assertEqual(sum(f"REF{i}" in panel for i in range(8)), 5)
        self.assertIn("+3 more", html)

    def test_next_redirect_is_guarded(self):
        wr = StoreWorkRequest.objects.create(project=self.store, kind="catalog", requested_by=self.a)
        r = self.client.post(reverse("control:crm_work_assign", kwargs={"pk": wr.pk}), {"assignee": self.b.pk, "next": "https://evil.test/"})
        self.assertNotIn("evil.test", r["Location"])

    def test_dgc_never_sees_the_inbox_or_can_use_it(self):
        Collection.objects.create(collected_by=self.a, amount=Decimal("5"), mode=CollectionMode.CASH, reference="X")
        self.login(self.b)
        self.assertEqual(self.client.get(self.board).status_code, 403)
        self.assertEqual(self.client.post(reverse("control:crm_work_assign", kwargs={"pk": 1}), {"assignee": self.b.pk}).status_code, 403)


class PipelineAndPeopleTests(CrmBase):
    def setUp(self):
        super().setUp()
        self.login(self.admin)

    def test_pipeline_strip_counts_and_links(self):
        Lead.objects.create(name="a", assigned_to=self.a, status=LeadStatus.NEW)
        Lead.objects.create(name="b", assigned_to=self.a, status=LeadStatus.INTERESTED)
        Lead.objects.create(name="c", assigned_to=self.b, status=LeadStatus.WON)
        Lead.objects.create(name="d", status=LeadStatus.NEW)                       # unassigned
        Lead.objects.create(name="e", assigned_to=self.a, is_archived=True)        # archived: not counted
        p = svc.pipeline_counts()
        self.assertEqual({k: n for k, _, n in p["stages"]}, {"new": 2, "contacted": 0, "interested": 1,
                                                              "demo_booked": 0, "demo_done": 0, "negotiating": 0})
        self.assertEqual((p["won"], p["unassigned"]), (1, 1))
        html = self.client.get(reverse("control:crm_board")).content.decode()
        self.assertIn("view=list&status=interested", html)
        self.assertIn("1 unassigned", html)
        self.assertIn("view=list&assignee=none", html)

    def test_last_active_column_and_label(self):
        svc.log_activity(actor=self.a, kind="call", outcome="connected")
        rows, _ = svc.person_numbers(timezone.localdate())
        by = {r["user"].pk: r for r in rows}
        self.assertEqual(by[self.a.pk]["last_active_label"], "just now")
        self.assertEqual(by[self.b.pk]["last_active_label"], "never")
        html = self.client.get(reverse("control:crm_board") + "?active=0").content.decode()
        self.assertIn("just now", html)
        self.assertIn("never", html)

    def test_time_ago_buckets(self):
        n = timezone.now()
        for delta, want in ((dt.timedelta(seconds=30), "just now"), (dt.timedelta(minutes=12), "12m ago"),
                            (dt.timedelta(hours=3, minutes=5), "3h ago"), (dt.timedelta(days=1, hours=2), "yesterday"),
                            (dt.timedelta(days=5), "5d ago")):
            self.assertEqual(svc.time_ago(n - delta), want)
        self.assertEqual(svc.time_ago(None), "never")

    def test_person_page_content_and_links(self):
        Lead.objects.create(name="L1", assigned_to=self.a, status=LeadStatus.INTERESTED,
                            next_follow_up=timezone.localdate() - dt.timedelta(days=1))
        svc.log_activity(actor=self.a, kind="call", outcome="connected")
        html = self.client.get(reverse("control:crm_person", kwargs={"pk": self.a.pk})).content.decode()
        self.assertIn("anil", html)
        self.assertIn("last active", html)
        self.assertIn("Recent activity", html)
        self.assertIn(f"assignee={self.a.pk}&status=interested", html)
        self.assertIn("1 overdue", html)
        self.assertIn("Transfer their open leads", html)
        self.assertIn(reverse("control:crm_task_create"), html)
        for q in ("?range=yesterday", "?range=7d", "?range=month"):
            self.assertEqual(self.client.get(reverse("control:crm_person", kwargs={"pk": self.a.pk}) + q).status_code, 200)

    def test_board_names_link_to_person_page(self):
        svc.log_activity(actor=self.a, kind="call", outcome="connected")
        html = self.client.get(reverse("control:crm_board")).content.decode()
        self.assertIn(reverse("control:crm_person", kwargs={"pk": self.a.pk}), html)

    def test_person_page_is_admin_only_and_404s_for_non_team(self):
        url = reverse("control:crm_person", kwargs={"pk": self.a.pk})
        self.login(self.b)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse("control:crm_person", kwargs={"pk": self.owner.pk})).status_code, 404)

    def test_task_from_person_page_returns_there(self):
        url = reverse("control:crm_person", kwargs={"pk": self.a.pk})
        r = self.client.post(reverse("control:crm_task_create"), {"title": "Call 20", "assignee": self.a.pk, "next": url})
        self.assertRedirects(r, url, fetch_redirect_response=False)
        self.assertEqual(Task.objects.get().assignee, self.a)


class DistributeTests(CrmBase):
    def setUp(self):
        super().setUp()
        self.c = _dgc("chetan")
        self.login(self.admin)

    def _mk(self, n, **kw):
        return Lead.objects.bulk_create([Lead(name=f"n{i}", **kw) for i in range(n)])

    def test_even_split_balances_against_existing_load(self):
        self._mk(6, assigned_to=self.a)                       # anil already holds 6 open leads
        new = [Lead.objects.create(name=f"x{i}") for i in range(6)]
        got = svc.distribute_leads(new, [self.a, self.b, self.c], actor=self.admin)
        self.assertEqual(sum(got.values()), 6)
        self.assertEqual(got[self.a.pk], 0)                   # the busiest gets none
        self.assertEqual((got[self.b.pk], got[self.c.pk]), (3, 3))
        self.assertTrue(all(Lead.objects.get(pk=l.pk).assigned_by == self.admin for l in new))

    def test_even_split_from_scratch_is_within_one(self):
        new = [Lead.objects.create(name=f"x{i}") for i in range(10)]
        got = svc.distribute_leads(new, [self.a, self.b, self.c], actor=self.admin)
        self.assertEqual(sorted(got.values()), [3, 3, 4])

    def test_archived_and_closed_leads_do_not_count_as_load(self):
        self._mk(5, assigned_to=self.a, is_archived=True)
        self._mk(5, assigned_to=self.a, status=LeadStatus.LOST)
        new = [Lead.objects.create(name="x")]
        got = svc.distribute_leads(new, [self.a, self.b], actor=self.admin)
        self.assertEqual(got[self.a.pk], 1)                   # tie at 0 -> lowest id (anil)

    def test_no_assignees_or_leads_is_a_noop(self):
        self.assertEqual(svc.distribute_leads([], [self.a], actor=self.admin), {})
        self.assertEqual(svc.distribute_leads([Lead.objects.create(name="x")], [], actor=self.admin), {})

    def test_panel_shares_all_unassigned_among_chosen_dgcs(self):
        self._mk(5)
        self._mk(2, assigned_to=self.a, status=LeadStatus.WON)   # untouched
        html = self.client.get(reverse("control:crm_leads") + "?view=list").content.decode()
        self.assertIn("5 unassigned", html)
        self.assertIn("Share 5 evenly", html)
        r = self.client.post(reverse("control:crm_lead_distribute"),
                             {"assignees": [self.a.pk, self.b.pk], "next": reverse("control:crm_leads") + "?view=list"}, follow=True)
        self.assertFalse(Lead.objects.filter(assigned_to__isnull=True).exists())
        self.assertEqual(Lead.objects.filter(assigned_to=self.c).count(), 0)         # not chosen
        self.assertEqual(sorted([Lead.objects.filter(assigned_to=u, status=LeadStatus.NEW).count() for u in (self.a, self.b)]), [2, 3])
        self.assertIn("Shared 5 lead(s) evenly", r.content.decode())

    def test_panel_hidden_when_nothing_unassigned(self):
        self._mk(2, assigned_to=self.a)
        self.assertNotIn("unassigned", self.client.get(reverse("control:crm_leads") + "?view=list").content.decode().split("<table")[0])

    def test_spread_ticked_leads_over_all_dgcs(self):
        leads = [Lead.objects.create(name=f"t{i}", assigned_to=self.a) for i in range(6)]
        self.client.post(reverse("control:crm_lead_distribute"), {"ids": [l.pk for l in leads]})
        counts = sorted(Lead.objects.filter(pk__in=[l.pk for l in leads], assigned_to=u).count() for u in (self.a, self.b, self.c))
        self.assertEqual(counts, [2, 2, 2])

    def test_distribute_ignores_archived_and_caps_ids(self):
        arch = Lead.objects.create(name="old", is_archived=True)
        self.client.post(reverse("control:crm_lead_distribute"), {"ids": [arch.pk]})
        arch.refresh_from_db()
        self.assertIsNone(arch.assigned_to)
        self.client.post(reverse("control:crm_lead_distribute"), {"ids": list(range(1, svc.BULK_ARCHIVE_MAX + 2))})
        self.assertFalse(Lead.objects.filter(assigned_to__isnull=False).exists())

    def test_distribute_with_unknown_assignees_refuses(self):
        self._mk(2)
        self.client.post(reverse("control:crm_lead_distribute"), {"assignees": [999999]})
        self.assertEqual(Lead.objects.filter(assigned_to__isnull=True).count(), 2)

    def test_distribute_is_admin_only_and_csrf_guarded(self):
        from django.test import Client
        self._mk(2)
        url = reverse("control:crm_lead_distribute")
        self.login(self.a)
        self.assertEqual(self.client.post(url, {}).status_code, 403)
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.admin)
        self.assertEqual(c.post(url, {}).status_code, 403)
        self.assertEqual(Lead.objects.filter(assigned_to__isnull=True).count(), 2)

    def test_bulk_bar_shows_spread_for_admin_only(self):
        Lead.objects.create(name="x", assigned_to=self.a)
        self.assertIn("Spread evenly", self.client.get(reverse("control:crm_leads") + "?view=list").content.decode())
        self.login(self.a)
        self.assertNotIn("Spread evenly", self.client.get(reverse("control:crm_leads") + "?view=list").content.decode())

    def test_import_can_split_evenly_among_all_dgcs(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        rows = "name,phone\n" + "\n".join(f"P{i},90000000{i:02d}" for i in range(9)) + "\n"
        r = self.client.post(reverse("control:crm_lead_import"),
                             {"file": SimpleUploadedFile("l.csv", rows.encode(), content_type="text/csv"), "spread": "1"}, follow=True)
        counts = [Lead.objects.filter(assigned_to=u).count() for u in (self.a, self.b, self.c)]
        self.assertEqual(counts, [3, 3, 3])
        self.assertIn("Split evenly across 3 DGC(s)", r.content.decode())

    def test_import_spread_is_ignored_for_dgcs(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.login(self.a)
        self.client.post(reverse("control:crm_lead_import"),
                         {"file": SimpleUploadedFile("l.csv", b"name,phone\nQ,9111100000\n", content_type="text/csv"), "spread": "1"})
        self.assertEqual(Lead.objects.get(name="Q").assigned_to, self.a)

    def test_import_box_offers_split_only_to_admin(self):
        self.assertIn("Split evenly among all DGCs", self.client.get(reverse("control:crm_leads") + "?view=list").content.decode())
        self.login(self.a)
        self.assertNotIn("Split evenly among all DGCs", self.client.get(reverse("control:crm_leads") + "?view=list").content.decode())


class TransferLeadsTests(CrmBase):
    def setUp(self):
        super().setUp()
        self.c = _dgc("chetan")
        self.login(self.admin)
        self.url = reverse("control:crm_person_transfer", kwargs={"pk": self.a.pk})
        self.mine = [Lead.objects.create(name=f"m{i}", assigned_to=self.a) for i in range(6)]
        self.keep_won = Lead.objects.create(name="won", assigned_to=self.a, status=LeadStatus.WON)
        self.keep_arch = Lead.objects.create(name="arch", assigned_to=self.a, is_archived=True)

    def test_transfer_to_one_person_moves_only_open_leads(self):
        self.client.post(self.url, {"to": self.b.pk})
        self.assertEqual(Lead.objects.filter(assigned_to=self.b).count(), 6)
        for l in (self.keep_won, self.keep_arch):
            l.refresh_from_db()
            self.assertEqual(l.assigned_to, self.a)                 # history/closed stays put

    def test_spread_goes_to_others_not_back_to_source(self):
        self.client.post(self.url, {"to": "spread"})
        self.assertEqual(Lead.objects.filter(assigned_to=self.a, status=LeadStatus.NEW, is_archived=False).count(), 0)
        self.assertEqual((Lead.objects.filter(assigned_to=self.b).count(), Lead.objects.filter(assigned_to=self.c).count()), (3, 3))

    def test_refuses_self_unknown_and_missing_target(self):
        for to in (str(self.a.pk), "", "999999", "nonsense"):
            self.client.post(self.url, {"to": to})
        self.assertEqual(Lead.objects.filter(assigned_to=self.a, is_archived=False, status=LeadStatus.NEW).count(), 6)

    def test_transfer_is_admin_only_and_person_must_be_team(self):
        self.login(self.b)
        self.assertEqual(self.client.post(self.url, {"to": self.c.pk}).status_code, 403)
        self.login(self.admin)
        self.assertEqual(self.client.post(reverse("control:crm_person_transfer", kwargs={"pk": self.owner.pk}), {"to": "spread"}).status_code, 404)

    def test_transfer_writes_audit_and_reports_count(self):
        from apps.core.models import AuditLog
        r = self.client.post(self.url, {"to": self.b.pk}, follow=True)
        self.assertIn("Moved 6 open lead(s)", r.content.decode())
        self.assertTrue(AuditLog.objects.filter(actor=self.admin, changes__crm_leads_distributed=6).exists())


from django.core import mail  # noqa: E402

from apps.crm.models import CrmProfile, CrmSettings, normalize_phone  # noqa: E402


class PhoneNormaliseTests(CrmBase):
    def test_normalize_variants(self):
        for raw in ("9876543210", "+91 98765 43210", "098765-43210", "(+91)9876543210", "91 9876543210"):
            self.assertEqual(normalize_phone(raw), "9876543210", raw)
        self.assertEqual(normalize_phone("12345"), "12345")
        self.assertEqual(normalize_phone(""), "")

    def test_saved_lead_gets_phone_norm_and_updates_on_edit(self):
        l = Lead.objects.create(name="x", phone="+91 98765 43210")
        self.assertEqual(l.phone_norm, "9876543210")
        l.phone = "98000 11111"
        l.save()
        l.refresh_from_db()
        self.assertEqual(l.phone_norm, "9800011111")

    def test_save_with_update_fields_still_stores_phone_norm(self):
        l = Lead.objects.create(name="x")
        l.phone = "9111122222"
        l.save(update_fields=["phone"])
        l.refresh_from_db()
        self.assertEqual(l.phone_norm, "9111122222")

    def test_duplicate_detection_ignores_formatting(self):
        Lead.objects.create(name="x", phone="9876543210")
        self.assertTrue(svc.phone_exists("+91 98765-43210"))
        self.assertFalse(svc.phone_exists("9000000000"))
        self.assertFalse(svc.phone_exists("123"))            # too short to trust

    def test_quick_add_and_import_use_the_normalised_check(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        Lead.objects.create(name="x", phone="9876543210")
        self.login(self.a)
        self.client.post(reverse("control:crm_lead_create"), {"phone": "+91 98765 43210"})
        self.client.post(reverse("control:crm_lead_import"), {"file": SimpleUploadedFile(
            "l.csv", b"name,phone\nDup,098765-43210\nFresh,9000000001\n", content_type="text/csv")})
        self.assertEqual(Lead.objects.count(), 2)
        self.assertTrue(Lead.objects.filter(name="Fresh").exists())


class RoutingTests(CrmBase):
    def setUp(self):
        super().setUp()
        self.c = _dgc("chetan")
        self.cfg = CrmSettings.load()
        mail.outbox.clear()

    def _in(self, **kw):
        kw.setdefault("name", "Inbound")
        kw.setdefault("phone", "9%09d" % (Lead.objects.count() + 1))
        return svc.ingest_lead(**kw)

    def test_ingest_creates_and_routes_to_least_loaded(self):
        for i in range(3):
            Lead.objects.create(name=f"a{i}", assigned_to=self.a)
        Lead.objects.create(name="b", assigned_to=self.b)
        lead, created = self._in()
        self.assertTrue(created)
        self.assertEqual(lead.assigned_to, self.c)            # holds nothing yet
        self.assertTrue(lead.auto_assigned)
        self.assertIsNotNone(lead.assigned_at)

    def test_routing_balances_over_many_leads(self):
        got = {self.a.pk: 0, self.b.pk: 0, self.c.pk: 0}
        for _ in range(9):
            lead, _c = self._in()
            got[lead.assigned_to_id] += 1
        self.assertEqual(sorted(got.values()), [3, 3, 3])

    def test_city_match_beats_load(self):
        CrmProfile.objects.create(user=self.a, cities="Pune, Nashik")
        for i in range(5):
            Lead.objects.create(name=f"a{i}", assigned_to=self.a)       # anil is busiest
        lead, _ = self._in(city="Pune")
        self.assertEqual(lead.assigned_to, self.a)                        # but he covers Pune
        other, _ = self._in(city="Delhi")
        self.assertNotEqual(other.assigned_to, self.a)

    def test_city_match_is_substring_both_ways_case_insensitive(self):
        CrmProfile.objects.create(user=self.b, cities="pimpri chinchwad, mumbai")
        self.assertEqual(self._in(city="MUMBAI")[0].assigned_to, self.b)
        self.assertEqual(self._in(city="Navi Mumbai")[0].assigned_to, self.b)

    def test_paused_dgc_is_skipped(self):
        CrmProfile.objects.create(user=self.a, accepts_leads=False)
        CrmProfile.objects.create(user=self.b, accepts_leads=False)
        self.assertEqual(self._in()[0].assigned_to, self.c)
        CrmProfile.objects.create(user=self.c, accepts_leads=False)
        lead, _ = self._in()
        self.assertIsNone(lead.assigned_to)                               # nobody available -> stays in pool

    def test_capacity_cap_and_overflow_to_pool(self):
        self.cfg.capacity_per_dgc = 2
        self.cfg.save()
        for u in (self.a, self.b, self.c):
            for i in range(2):
                Lead.objects.create(name=f"{u.pk}-{i}", assigned_to=u)
        lead, _ = self._in()
        self.assertIsNone(lead.assigned_to)                               # everyone is full
        Lead.objects.filter(assigned_to=self.b).update(is_archived=True)  # frees capacity
        self.assertEqual(self._in()[0].assigned_to, self.b)

    def test_auto_assign_switch_off_leaves_lead_unassigned(self):
        self.cfg.auto_assign = False
        self.cfg.save()
        lead, created = self._in()
        self.assertTrue(created)
        self.assertIsNone(lead.assigned_to)

    def test_duplicate_phone_changes_nothing_and_returns_existing(self):
        first, c1 = self._in(phone="9876543210")
        again, c2 = svc.ingest_lead(name="Other name", phone="+91 98765 43210")
        self.assertEqual((c1, c2), (True, False))
        self.assertEqual(again.pk, first.pk)
        self.assertEqual(Lead.objects.count(), 1)

    def test_referring_dgc_keeps_the_lead(self):
        lead, _ = svc.ingest_lead(name="Ref", phone="9300000001", assign_to=self.b)
        self.assertEqual(lead.assigned_to, self.b)
        self.assertFalse(lead.auto_assigned)

    def test_needs_name_or_phone(self):
        with self.assertRaises(ValueError):
            svc.ingest_lead(name="", phone="")
        lead, _ = svc.ingest_lead(phone="9400000001")
        self.assertEqual(lead.name, "9400000001")

    def test_alert_email_sent_to_assignee_and_can_be_switched_off(self):
        lead, _ = self._in(name="Hot Lead", city="Pune")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [lead.assigned_to.email])
        self.assertIn("Hot Lead", mail.outbox[0].subject)
        self.assertIn("5 minutes", mail.outbox[0].body)
        mail.outbox.clear()
        self.cfg.alert_email = False
        self.cfg.save()
        self._in()
        self.assertEqual(len(mail.outbox), 0)

    def test_alert_failure_never_breaks_intake(self):
        from unittest import mock
        with mock.patch("django.core.mail.send_mail", side_effect=RuntimeError("smtp down")):
            lead, created = self._in()
        self.assertTrue(created)
        self.assertIsNotNone(lead.assigned_to)

    def test_assigned_at_tracks_assignment_changes(self):
        lead = Lead.objects.create(name="x")
        self.assertIsNone(lead.assigned_at)
        lead.assigned_to = self.a
        lead.save()
        first = lead.assigned_at
        self.assertIsNotNone(first)
        lead.notes = "edit"
        lead.save()
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_at, first)                        # unrelated edit: clock untouched
        lead.assigned_to = self.b
        lead.save()
        lead.refresh_from_db()
        self.assertGreaterEqual(lead.assigned_at, first)                 # reassigned: restarts

    def test_first_touch_recorded_once_by_real_outreach_only(self):
        lead = Lead.objects.create(name="x", assigned_to=self.a)
        svc.log_activity(actor=self.a, kind="product_entry", lead=lead)
        lead.refresh_from_db()
        self.assertIsNone(lead.first_touch_at)
        svc.log_activity(actor=self.a, kind="call", outcome="no_answer", lead=lead)
        lead.refresh_from_db()
        first = lead.first_touch_at
        self.assertIsNotNone(first)
        svc.log_activity(actor=self.a, kind="call", outcome="connected", lead=lead)
        lead.refresh_from_db()
        self.assertEqual(lead.first_touch_at, first)


class QueuePriorityTests(CrmBase):
    def _q(self):
        return [l.name for l in svc.call_queue(self.a)]

    def test_fresh_untouched_inbound_jumps_ahead_of_due_followups(self):
        old_due = Lead.objects.create(name="due", assigned_to=self.a, status=LeadStatus.CONTACTED,
                                      next_follow_up=timezone.localdate() - dt.timedelta(days=3))
        new = Lead.objects.create(name="fresh", assigned_to=self.a)
        Lead.objects.filter(pk=new.pk).update(assigned_at=timezone.now())
        self.assertEqual(self._q()[0], "fresh")
        svc.log_activity(actor=self.a, kind="call", outcome="no_answer", lead=Lead.objects.get(pk=new.pk))
        self.assertEqual(self._q(), ["due"])                              # touched -> no longer jumps

    def test_stale_assignment_is_not_fresh(self):
        old = Lead.objects.create(name="old", assigned_to=self.a)
        Lead.objects.filter(pk=old.pk).update(assigned_at=timezone.now() - dt.timedelta(hours=svc.FRESH_HOURS + 1))
        hot = Lead.objects.create(name="hot", assigned_to=self.a, status=LeadStatus.NEGOTIATING,
                                  next_follow_up=timezone.localdate())
        self.assertEqual(self._q()[0], "hot")

    def test_hotter_stages_before_colder_when_nothing_else_differs(self):
        names = {}
        for st in ("new", "contacted", "interested", "demo_booked", "demo_done", "negotiating"):
            names[st] = Lead.objects.create(name=st, assigned_to=self.a, status=st,
                                            next_follow_up=timezone.localdate()).name
        order = self._q()
        self.assertEqual(order, ["negotiating", "demo_done", "demo_booked", "interested", "contacted", "new"])

    def test_due_followup_still_beats_a_worked_today_lead(self):
        due = Lead.objects.create(name="due", assigned_to=self.a, next_follow_up=timezone.localdate())
        done = Lead.objects.create(name="done", assigned_to=self.a)
        svc.log_activity(actor=self.a, kind="call", outcome="connected", lead=done)
        self.assertEqual(self._q(), ["due"])


class SignupHookTests(CrmBase):
    def _project(self, **src):
        p = Project.objects.create(name="Ravi Jewels", status="active", signup_source=src)
        return Project.objects.get(pk=p.pk)

    def test_signup_becomes_a_routed_lead_with_attribution(self):
        p = self._project(utm_source="facebook", lp="jewellery", gclid="g1")
        lead, created = svc.lead_from_signup(p, name="Ravi Kumar", phone="9811100000", city="Pune")
        self.assertTrue(created)
        self.assertEqual((lead.name, lead.business, lead.city), ("Ravi Kumar", "Ravi Jewels", "Pune"))
        self.assertEqual(lead.source, "Signup · facebook")
        self.assertEqual(lead.acquisition["lp"], "jewellery")
        self.assertEqual(lead.converted_project, p)
        self.assertIsNotNone(lead.assigned_to)
        self.assertIn("Self-signup", lead.notes)

    def test_referring_dgc_gets_their_own_signup(self):
        p = self._project()
        lead, _ = svc.lead_from_signup(p, name="R", phone="9822200000", ref_user=self.b)
        self.assertEqual(lead.assigned_to, self.b)
        self.assertEqual(lead.source, "Signup · organic")

    def test_second_signup_with_same_phone_is_not_duplicated(self):
        svc.lead_from_signup(self._project(), name="R", phone="9833300000")
        svc.lead_from_signup(self._project(), name="R2", phone="+91 98333 00000")
        self.assertEqual(Lead.objects.count(), 1)

    def test_view_helper_never_breaks_signup_and_resolves_ref_code(self):
        from unittest import mock
        from apps.accounts.views import _crm_lead_from_signup
        p = self._project()
        prof = Profile.objects.get(user=self.b)
        code = prof.ensure_affiliate_code()
        _crm_lead_from_signup(p, name="R", phone="9844400000", city="", ref_code=code)
        self.assertEqual(Lead.objects.get(phone="9844400000").assigned_to, self.b)
        with mock.patch("apps.crm.services.lead_from_signup", side_effect=RuntimeError("boom")):
            _crm_lead_from_signup(p, name="R", phone="9855500000", city="", ref_code="")   # must not raise
        self.assertFalse(Lead.objects.filter(phone="9855500000").exists())

    def test_unknown_ref_code_is_ignored(self):
        from apps.accounts.views import _crm_lead_from_signup
        _crm_lead_from_signup(self._project(), name="R", phone="9866600000", city="", ref_code="NOPE1234")
        self.assertIsNotNone(Lead.objects.get(phone="9866600000").assigned_to)   # auto-routed instead


@override_settings(CRM_CAPTURE_TOKEN="s3cret-token", ALLOWED_HOSTS=["*"])
class CaptureWebhookTests(CrmBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.url = reverse("crm_capture")

    def _post(self, data, token="s3cret-token", **kw):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        headers = {"HTTP_X_CRM_TOKEN": token} if token else {}
        return c.post(self.url, data=json.dumps(data), content_type="application/json", **headers, **kw)

    def test_creates_and_routes_json_lead_without_csrf(self):
        r = self._post({"name": "Meta Lead", "phone": "9877700000", "city": "Pune", "source": "Meta lead ad",
                        "utm_campaign": "diwali", "junk": "ignored"})
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertTrue(body["ok"] and body["created"])
        lead = Lead.objects.get(pk=body["lead_id"])
        self.assertEqual((lead.name, lead.source, lead.acquisition), ("Meta Lead", "Meta lead ad", {"utm_campaign": "diwali"}))
        self.assertIsNotNone(lead.assigned_to)
        self.assertEqual(body["assigned"], svc.person_label(lead.assigned_to))

    def test_form_encoded_and_bearer_token(self):
        from django.test import Client
        r = Client().post(self.url, {"name": "Form Lead", "phone": "9888800000"}, HTTP_AUTHORIZATION="Bearer s3cret-token")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Lead.objects.get(phone="9888800000").source, "capture")

    def test_duplicate_returns_200_and_created_false(self):
        self._post({"name": "A", "phone": "9877711111"})
        r = self._post({"name": "B", "phone": "+91 98777 11111"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["created"])
        self.assertEqual(Lead.objects.count(), 1)

    def test_wrong_or_missing_token_is_forbidden_and_creates_nothing(self):
        self.assertEqual(self._post({"name": "x", "phone": "9000000009"}, token="nope").status_code, 403)
        self.assertEqual(self._post({"name": "x", "phone": "9000000009"}, token="").status_code, 403)
        self.assertFalse(Lead.objects.exists())

    @override_settings(CRM_CAPTURE_TOKEN="")
    def test_feature_is_off_without_a_configured_token(self):
        self.assertEqual(self._post({"name": "x", "phone": "9000000009"}, token="anything").status_code, 404)

    def test_validation_errors(self):
        self.assertEqual(self._post({"city": "Pune"}).status_code, 400)                         # no name/phone
        from django.test import Client
        r = Client().post(self.url, data="{not json", content_type="application/json", HTTP_X_CRM_TOKEN="s3cret-token")
        self.assertEqual(r.status_code, 400)
        r = Client().post(self.url, data="[1,2]", content_type="application/json", HTTP_X_CRM_TOKEN="s3cret-token")
        self.assertEqual(r.status_code, 400)

    def test_only_post_and_size_limit_and_field_truncation(self):
        from django.test import Client
        self.assertEqual(Client().get(self.url).status_code, 405)
        r = self._post({"name": "n", "phone": "9", "notes": "x" * 20000})
        self.assertEqual(r.status_code, 413)
        r = self._post({"name": "N" * 500, "phone": "9123400000"})
        self.assertEqual(len(Lead.objects.get(pk=r.json()["lead_id"]).name), 120)

    def test_rate_limit(self):
        for i in range(120):
            cache.set("crm_capture:127.0.0.1", i, 60)
        cache.set("crm_capture:127.0.0.1", 120, 60)
        self.assertEqual(self._post({"name": "x", "phone": "9000000009"}).status_code, 429)


class SettingsAndProfileTests(CrmBase):
    def test_settings_screen_admin_only(self):
        url = reverse("control:crm_settings")
        self.login(self.a)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {}).status_code, 403)
        self.login(self.admin)
        html = self.client.get(url).content.decode()
        for needle in ("Speed-to-lead", "Automatic follow-ups", "Admin alerts", "Forecast", "Lead capture webhook"):
            self.assertIn(needle, html)

    def test_settings_save_including_stage_probabilities(self):
        self.login(self.admin)
        data = {"auto_assign": "on", "capacity_per_dgc": "25", "trial_rescue_days": "2",
                "health_no_products_days": "5", "health_no_orders_days": "10", "recycle_days": "20",
                "quiet_after_hour": "11", "avg_plan_price": "3499", "digest_emails": "a@x.com, b@y.com",
                "p_new": "5", "p_contacted": "10", "p_interested": "25", "p_demo_booked": "35",
                "p_demo_done": "50", "p_negotiating": "70"}
        r = self.client.post(reverse("control:crm_settings"), data)
        self.assertEqual(r.status_code, 302)
        cfg = CrmSettings.load()
        self.assertEqual((cfg.capacity_per_dgc, cfg.trial_rescue_days, cfg.quiet_after_hour), (25, 2, 11))
        self.assertFalse(cfg.alert_email)                                  # unchecked box -> off
        self.assertEqual(cfg.digest_emails, "a@x.com, b@y.com")
        self.assertEqual((cfg.probability("new"), cfg.probability("negotiating")), (5, 70))

    def test_settings_validation(self):
        self.login(self.admin)
        base = {"capacity_per_dgc": "0", "trial_rescue_days": "3", "health_no_products_days": "7",
                "health_no_orders_days": "14", "recycle_days": "30", "quiet_after_hour": "12", "avg_plan_price": "2999",
                "p_new": "3", "p_contacted": "8", "p_interested": "20", "p_demo_booked": "30", "p_demo_done": "45", "p_negotiating": "65"}
        for bad in ({"quiet_after_hour": "30"}, {"digest_emails": "not-an-email"}, {"p_new": "150"}):
            r = self.client.post(reverse("control:crm_settings"), {**base, **bad})
            self.assertEqual(r.status_code, 200, bad)                      # re-rendered with errors
        self.assertEqual(CrmSettings.load().quiet_after_hour, 12)

    def test_probability_defaults_and_clamping(self):
        cfg = CrmSettings.load()
        self.assertEqual(cfg.probability("demo_done"), 45)
        cfg.stage_probabilities = {"new": 999, "contacted": "bad"}
        self.assertEqual((cfg.probability("new"), cfg.probability("contacted")), (100, 0))

    def test_dgc_pauses_and_sets_cities_only_for_themselves(self):
        self.login(self.a)
        self.client.post(reverse("control:crm_my_profile"), {"cities": " Pune ,Mumbai,, "})        # unchecked -> paused
        p = CrmProfile.objects.get(user=self.a)
        self.assertEqual((p.accepts_leads, p.cities), (False, "Pune, Mumbai"))
        self.assertFalse(CrmProfile.objects.filter(user=self.b).exists())
        self.client.post(reverse("control:crm_my_profile"), {"accepts_leads": "1", "cities": "Pune"})
        self.assertTrue(CrmProfile.objects.get(user=self.a).accepts_leads)

    def test_my_day_shows_fresh_banner_and_settings_panel(self):
        lead, _ = svc.ingest_lead(name="Hot One", phone="9600000001", city="Pune", source="Signup · facebook",
                                  assign_to=self.a)
        Lead.objects.filter(pk=lead.pk).update(assigned_at=timezone.now())
        self.login(self.a)
        html = self.client.get(reverse("control:crm_my_day")).content.decode()
        self.assertIn("1 new lead for you", html)
        self.assertIn("call within 5 minutes", html)
        self.assertIn("Hot One", html)
        self.assertIn("Signup · facebook", html)
        self.assertIn("My lead settings", html)
        self.assertIn("taking new leads", html)
        svc.log_activity(actor=self.a, kind="call", outcome="no_answer", lead=Lead.objects.get(pk=lead.pk))
        self.assertNotIn("new lead for you", self.client.get(reverse("control:crm_my_day")).content.decode())

    def test_paused_label_shows_after_pausing(self):
        CrmProfile.objects.create(user=self.a, accepts_leads=False)
        self.login(self.a)
        self.assertIn("· paused", self.client.get(reverse("control:crm_my_day")).content.decode())

    def test_board_links_to_settings_for_admin(self):
        self.login(self.admin)
        self.assertIn(reverse("control:crm_settings"), self.client.get(reverse("control:crm_board")).content.decode())


from apps.crm import automation as auto  # noqa: E402
from apps.catalog.models import Product  # noqa: E402
from apps.orders.models import Order  # noqa: E402
from apps.billing.models import SubscriptionStatus  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


class AutomationBase(CrmBase):
    def setUp(self):
        super().setUp()
        self.cfg = CrmSettings.load()
        mail.outbox.clear()

    def mkstore(self, name="Ravi Jewels", *, days_old=0, trial_in=None, status=SubscriptionStatus.TRIALING,
              manager=None, referred_by=None):
        p = Project.objects.create(name=name, status="active")
        if days_old:
            Project.objects.filter(pk=p.pk).update(created_at=timezone.now() - dt.timedelta(days=days_old))
        sub = billing.ensure_subscription(Project.objects.get(pk=p.pk))
        sub.status, sub.manager, sub.referred_by = status, manager, referred_by
        if trial_in is not None:
            sub.trial_end = timezone.now() + trial_in
        sub.save()
        return Project.objects.get(pk=p.pk)


class ResponsibleDgcTests(AutomationBase):
    def test_priority_lead_owner_then_manager_then_referrer(self):
        p = self.mkstore(manager=self.b, referred_by=self.owner)
        self.assertEqual(auto.responsible_dgc(p), self.b)                       # manager
        Lead.objects.create(name="L", converted_project=p, assigned_to=self.a)
        self.assertEqual(auto.responsible_dgc(p), self.a)                       # the lead's owner beats the manager
        p2 = self.mkstore("Other", referred_by=self.a)
        self.assertEqual(auto.responsible_dgc(p2), self.a)                      # referrer last
        self.assertIsNone(auto.responsible_dgc(self.mkstore("Orphan")))

    def test_inactive_dgc_is_skipped(self):
        p = self.mkstore(manager=self.b, referred_by=self.a)
        User.objects.filter(pk=self.b.pk).update(is_active=False)
        self.assertEqual(auto.responsible_dgc(Project.objects.get(pk=p.pk)), self.a)


class TrialRescueTests(AutomationBase):
    def test_creates_one_task_for_the_responsible_dgc_with_context(self):
        p = self.mkstore("Ravi Jewels", trial_in=dt.timedelta(days=2, hours=1), manager=self.a)
        Membership.objects.create(project=p, user=self.owner, role=StoreRole.OWNER)
        Profile.objects.filter(user=self.owner).update(phone="9811122233")
        self.assertEqual(auto.trial_rescue(), 1)
        t = Task.objects.get()
        self.assertEqual((t.assignee, t.project), (self.a, p))
        self.assertIn("Ravi Jewels", t.title)
        self.assertIn("in 2 days", t.title)
        self.assertIn("9811122233", t.detail)
        self.assertTrue(t.auto_key.startswith("trial:"))
        self.assertEqual(t.due_on, timezone.localdate())

    def test_idempotent_across_runs_even_if_task_is_done(self):
        self.mkstore(trial_in=dt.timedelta(days=1), manager=self.a)
        auto.trial_rescue()
        svc.mark_task_done(Task.objects.get())
        self.assertEqual(auto.trial_rescue(), 0)
        self.assertEqual(Task.objects.count(), 1)

    def test_only_trials_inside_the_window_that_are_not_comped(self):
        self.mkstore("Far", trial_in=dt.timedelta(days=10), manager=self.a)
        self.mkstore("Active", trial_in=dt.timedelta(days=1), manager=self.a, status=SubscriptionStatus.ACTIVE)
        comp = self.mkstore("Comp", trial_in=dt.timedelta(days=1), manager=self.a)
        Subscription = type(comp.subscription)
        Subscription.objects.filter(project=comp).update(is_comp=True)
        past = self.mkstore("Past", trial_in=-dt.timedelta(days=1), manager=self.a)
        self.assertEqual(auto.trial_rescue(), 0)
        self.assertFalse(Task.objects.exists())

    def test_no_responsible_dgc_means_no_task(self):
        self.mkstore(trial_in=dt.timedelta(days=1))
        self.assertEqual(auto.trial_rescue(), 0)

    def test_disabled_when_days_is_zero_and_window_is_configurable(self):
        self.mkstore(trial_in=dt.timedelta(days=5), manager=self.a)
        self.cfg.trial_rescue_days = 0
        self.cfg.save()
        self.assertEqual(auto.trial_rescue(), 0)
        self.cfg.trial_rescue_days = 7
        self.cfg.save()
        self.assertEqual(auto.trial_rescue(), 1)

    def test_signup_lead_owner_is_the_one_who_gets_the_rescue_task(self):
        p = self.mkstore(trial_in=dt.timedelta(days=1))
        svc.lead_from_signup(p, name="R", phone="9777700000", ref_user=self.b)
        auto.trial_rescue()
        self.assertEqual(Task.objects.get().assignee, self.b)

    def test_one_bad_row_does_not_sink_the_batch(self):
        from unittest import mock
        self.mkstore("First", trial_in=dt.timedelta(days=1), manager=self.a)
        self.mkstore("Second", trial_in=dt.timedelta(days=1), manager=self.b)
        real = auto.responsible_dgc
        calls = {"n": 0}

        def flaky(project):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return real(project)
        with mock.patch.object(auto, "responsible_dgc", flaky):
            self.assertEqual(auto.trial_rescue(), 1)


class StoreHealthTests(AutomationBase):
    def _product(self, p):
        return Product.objects.create(project=p, title="P", price=Decimal("100"))

    def test_store_with_no_products_after_threshold_gets_a_task(self):
        p = self.mkstore("Empty Co", days_old=8, manager=self.a)
        self.assertEqual(auto.store_health(), 1)
        t = Task.objects.get()
        self.assertEqual((t.assignee, t.project, t.auto_key), (self.a, p, f"noprod:{p.pk}"))
        self.assertIn("no products", t.title)
        self.assertEqual(auto.store_health(), 0)                                  # idempotent

    def test_young_store_or_one_with_products_is_left_alone(self):
        self.mkstore("Young", days_old=2, manager=self.a)
        stocked = self.mkstore("Stocked", days_old=9, manager=self.a)
        self._product(stocked)
        self.assertEqual(auto.store_health(), 0)

    def test_no_orders_nudge_is_monthly_and_only_for_stocked_stores(self):
        p = self.mkstore("Quiet Shop", days_old=20, manager=self.a)
        self._product(p)
        self.assertEqual(auto.store_health(), 1)
        t = Task.objects.get()
        self.assertTrue(t.auto_key.startswith(f"noorders:{p.pk}:"))
        self.assertIn("no orders", t.title)
        self.assertEqual(auto.store_health(), 0)                                  # same month: no repeat

    def test_store_with_an_order_is_healthy(self):
        p = self.mkstore("Selling", days_old=30, manager=self.a)
        self._product(p)
        Order.objects.create(project=p, number="O1", email="c@x.com")
        self.assertEqual(auto.store_health(), 0)

    def test_no_dgc_cancelled_or_disabled_means_no_task(self):
        self.mkstore("Nobody", days_old=30)
        self.mkstore("Dead", days_old=30, manager=self.a, status=SubscriptionStatus.CANCELLED)
        self.assertEqual(auto.store_health(), 0)
        self.mkstore("Empty", days_old=30, manager=self.a)
        self.cfg.health_no_products_days = 0
        self.cfg.save()
        self.assertEqual(auto.store_health(), 0)


class RecycleTests(AutomationBase):
    def _old(self, name, days=40, **kw):
        l = Lead.objects.create(name=name, assigned_to=self.a, **kw)
        Lead.objects.filter(pk=l.pk).update(created_at=timezone.now() - dt.timedelta(days=days))
        return Lead.objects.get(pk=l.pk)

    def test_cold_untouched_leads_return_to_the_pool_with_a_note(self):
        l = self._old("Cold")
        self.assertEqual(auto.recycle_leads(), 1)
        l.refresh_from_db()
        self.assertIsNone(l.assigned_to)
        self.assertIn("returned to pool", l.notes)
        self.assertEqual(auto.recycle_leads(), 0)

    def test_recent_activity_hot_stage_future_followup_young_or_archived_stay(self):
        touched = self._old("Touched")
        svc.log_activity(actor=self.a, kind="call", outcome="connected", lead=touched)
        hot = self._old("Hot", status=LeadStatus.INTERESTED)
        planned = self._old("Planned", next_follow_up=timezone.localdate() + dt.timedelta(days=3))
        young = self._old("Young", days=5)
        arch = self._old("Arch", is_archived=True)
        self.assertEqual(auto.recycle_leads(), 0)
        for l in (touched, hot, planned, young, arch):
            l.refresh_from_db()
            self.assertEqual(l.assigned_to, self.a, l.name)

    def test_threshold_and_off_switch(self):
        self._old("Mid", days=20)
        self.assertEqual(auto.recycle_leads(), 0)
        self.cfg.recycle_days = 15
        self.cfg.save()
        self.assertEqual(auto.recycle_leads(), 1)
        self._old("Again", days=99)
        self.cfg.recycle_days = 0
        self.cfg.save()
        self.assertEqual(auto.recycle_leads(), 0)

    def test_recycled_lead_appears_in_the_unassigned_pool_for_the_admin(self):
        self._old("Cold")
        auto.recycle_leads()
        self.assertEqual(svc.pipeline_counts()["unassigned"], 1)


class AdminAlertTests(AutomationBase):
    wed_1pm = dt.datetime(2026, 10, 7, 13, 0, tzinfo=IST)          # a Wednesday
    mon_9am = dt.datetime(2026, 10, 5, 9, 0, tzinfo=IST)           # a Monday

    def test_quiet_alert_lists_only_dgcs_with_no_activity_today(self):
        Activity.objects.create(actor=self.a, kind="call", occurred_at=self.wed_1pm - dt.timedelta(hours=3))
        self.assertEqual(auto.quiet_alert(self.wed_1pm), 1)
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertIn("bina", m.body)
        self.assertNotIn("anil", m.body)
        self.assertEqual(m.to, [self.admin.email])

    def test_quiet_alert_is_once_a_day_and_respects_hour_sunday_and_switch(self):
        self.assertEqual(auto.quiet_alert(self.wed_1pm.replace(hour=9)), 0)               # too early
        self.assertEqual(auto.quiet_alert(self.wed_1pm.replace(day=11)), 0)               # a Sunday
        self.assertEqual(auto.quiet_alert(self.wed_1pm), 2)
        self.assertEqual(auto.quiet_alert(self.wed_1pm + dt.timedelta(hours=2)), 0)       # already sent today
        self.assertEqual(auto.quiet_alert(self.wed_1pm + dt.timedelta(days=1)), 2)        # next day: again
        self.cfg.refresh_from_db()
        self.cfg.quiet_check = False
        self.cfg.save()
        self.assertEqual(auto.quiet_alert(self.wed_1pm + dt.timedelta(days=2)), 0)

    def test_quiet_alert_uses_india_time_not_utc(self):
        # 13:00 IST == 07:30 UTC: the hour gate must read IST (>= 12), not the UTC hour (7)
        self.assertEqual(auto.quiet_alert(self.wed_1pm.astimezone(dt.timezone.utc)), 2)

    def test_no_email_when_everyone_is_active_or_nobody_to_tell(self):
        for u in (self.a, self.b):
            Activity.objects.create(actor=u, kind="call", occurred_at=self.wed_1pm - dt.timedelta(hours=2))
        self.assertEqual(auto.quiet_alert(self.wed_1pm), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_digest_content_gate_and_once_per_week(self):
        start = self.mon_9am - dt.timedelta(days=3)
        for _ in range(4):
            Activity.objects.create(actor=self.a, kind="call", outcome="connected", occurred_at=start)
        Activity.objects.create(actor=self.a, kind="demo", occurred_at=start)
        self.assertEqual(auto.weekly_digest(self.wed_1pm), 0)                              # not Monday
        self.assertEqual(auto.weekly_digest(self.mon_9am.replace(hour=6)), 0)              # too early
        self.assertEqual(auto.weekly_digest(self.mon_9am), 1)
        body = mail.outbox[0].body
        self.assertIn("anil: 4 calls, 1 demos", body)
        self.assertIn("No activity all week: bina", body)
        self.assertIn("Pipeline:", body)
        self.assertEqual(auto.weekly_digest(self.mon_9am + dt.timedelta(hours=3)), 0)      # once that day
        self.assertEqual(auto.weekly_digest(self.mon_9am + dt.timedelta(days=7)), 1)       # next Monday

    def test_digest_recipients_and_switch(self):
        self.cfg.digest_emails = "boss@x.com, ops@x.com"
        self.cfg.save()
        auto.weekly_digest(self.mon_9am)
        self.assertEqual(sorted(mail.outbox[0].to), ["boss@x.com", "ops@x.com"])
        mail.outbox.clear()
        self.cfg.weekly_digest = False
        self.cfg.last_digest_on = None
        self.cfg.save()
        self.assertEqual(auto.weekly_digest(self.mon_9am), 0)

    def test_admin_emails_default_to_platform_admins(self):
        self.assertEqual(auto.admin_emails(), [self.admin.email])


class AutomationWiringTests(AutomationBase):
    def test_beat_schedule_points_at_real_tasks(self):
        from django.conf import settings as dj
        from apps.crm import tasks
        names = {v["task"] for k, v in dj.CELERY_BEAT_SCHEDULE.items() if k.startswith("crm-")}
        self.assertEqual(names, {"apps.crm.tasks.trial_rescue_task", "apps.crm.tasks.store_health_task",
                                 "apps.crm.tasks.recycle_leads_task", "apps.crm.tasks.admin_alerts_task"})
        for fn in (tasks.trial_rescue_task, tasks.store_health_task, tasks.recycle_leads_task, tasks.admin_alerts_task):
            fn()                                                                          # runs cleanly on an empty DB

    def test_run_now_is_admin_only_runs_jobs_and_reports(self):
        self.mkstore(trial_in=dt.timedelta(days=1), manager=self.a)
        url = reverse("control:crm_run_now")
        self.login(self.a)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertFalse(Task.objects.exists())
        self.login(self.admin)
        r = self.client.post(url, follow=True)
        self.assertEqual(Task.objects.count(), 1)
        self.assertIn("1 trial task(s)", r.content.decode())
        self.client.post(url)
        self.assertEqual(Task.objects.count(), 1)                                         # safe to press twice
        self.assertIn("Run the automatic jobs now", self.client.get(reverse("control:crm_settings")).content.decode())

    def test_auto_tasks_show_up_on_the_dgcs_my_day(self):
        self.mkstore("Ravi Jewels", trial_in=dt.timedelta(days=1), manager=self.a)
        auto.trial_rescue()
        self.login(self.a)
        self.assertIn("Ravi Jewels", self.client.get(reverse("control:crm_my_day")).content.decode())


from apps.crm.models import MessageTemplate, SourceSpend, TemplateKind  # noqa: E402
from apps.billing.models import ManagerCommission  # noqa: E402


class TemplateTests(CrmBase):
    def test_defaults_are_seeded_by_the_migration(self):
        self.assertEqual(MessageTemplate.objects.filter(kind="whatsapp").count(), 4)
        self.assertEqual(MessageTemplate.objects.filter(kind="script").count(), 3)
        self.assertTrue(MessageTemplate.objects.filter(kind="script", stage="negotiating").exists())

    def test_placeholders_are_filled_and_nothing_is_evaluated(self):
        lead = Lead(name="Ravi Kumar", business="Ravi Jewels", city="Pune")
        out = svc.render_template_text("Hi {first_name} / {name} / {business} / {city} / {dgc} / {0.__class__} {unknown}", lead, self.a)
        self.assertEqual(out, "Hi Ravi / Ravi Kumar / Ravi Jewels / Pune / anil / {0.__class__} {unknown}")

    def test_number_only_name_and_blank_fields_degrade_gracefully(self):
        out = svc.render_template_text("Hi {first_name}, {business} in {city}", Lead(name="9876543210"), self.a)
        self.assertEqual(out, "Hi there, your shop in your city")

    def test_whatsapp_links_encode_the_message_and_use_the_indian_number(self):
        MessageTemplate.objects.all().delete()
        MessageTemplate.objects.create(kind="whatsapp", title="Hi", body="Hello {first_name}! & more", order=1)
        links = svc.whatsapp_links(Lead(name="Ravi", phone="98765 43210"), self.a)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["url"], "https://wa.me/919876543210?text=Hello%20Ravi%21%20%26%20more")
        self.assertEqual(links[0]["text"], "Hello Ravi! & more")

    def test_no_phone_no_links_and_inactive_hidden_and_order_respected(self):
        self.assertEqual(svc.whatsapp_links(Lead(name="x"), self.a), [])
        MessageTemplate.objects.all().delete()
        MessageTemplate.objects.create(kind="whatsapp", title="B", body="b", order=2)
        MessageTemplate.objects.create(kind="whatsapp", title="A", body="a", order=1)
        MessageTemplate.objects.create(kind="whatsapp", title="Off", body="o", order=0, is_active=False)
        self.assertEqual([w["title"] for w in svc.whatsapp_links(Lead(name="x", phone="9000000000"), self.a)], ["A", "B"])

    def test_scripts_match_the_lead_stage_plus_any_stage_ones(self):
        MessageTemplate.objects.all().delete()
        MessageTemplate.objects.create(kind="script", stage="new", title="For new", body="n {name}")
        MessageTemplate.objects.create(kind="script", stage="negotiating", title="For nego", body="x")
        MessageTemplate.objects.create(kind="script", stage="", title="Always", body="a")
        got = [s["title"] for s in svc.scripts_for(Lead(name="Z", status="new"), self.a)]
        self.assertEqual(sorted(got), ["Always", "For new"])
        self.assertEqual(svc.scripts_for(Lead(name="Z", status="new"), self.a)[0]["text"][:1] in ("n", "a"), True)

    def test_lead_page_and_my_day_show_whatsapp_and_scripts(self):
        lead = Lead.objects.create(name="Ravi Kumar", phone="9876543210", business="Ravi Jewels", assigned_to=self.a)
        self.login(self.a)
        html = self.client.get(reverse("control:crm_lead", kwargs={"pk": lead.pk})).content.decode()
        self.assertIn("Send a WhatsApp message", html)
        self.assertIn("https://wa.me/919876543210?text=", html)
        self.assertIn("Opening call", html)                      # script for stage 'new'
        self.assertIn("Hello, am I speaking with Ravi Kumar?", html)
        self.assertNotIn("Handling objections", html)            # other stage
        day = self.client.get(reverse("control:crm_my_day")).content.decode()
        self.assertIn("Send a WhatsApp message", day)
        self.assertIn("wa.me/919876543210", day)

    def test_no_phone_no_whatsapp_block(self):
        lead = Lead.objects.create(name="NoPhone", assigned_to=self.a)
        self.login(self.a)
        self.assertNotIn("Send a WhatsApp message", self.client.get(reverse("control:crm_lead", kwargs={"pk": lead.pk})).content.decode())

    def test_admin_crud_and_dgc_blocked(self):
        for url in (reverse("control:crm_templates"), reverse("control:crm_template_new")):
            self.login(self.a)
            self.assertEqual(self.client.get(url).status_code, 403)
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse("control:crm_templates")).status_code, 200)
        r = self.client.post(reverse("control:crm_template_new"),
                             {"kind": "whatsapp", "stage": "negotiating", "title": "Thanks", "body": "Thanks {first_name}!", "order": "5", "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        t = MessageTemplate.objects.get(title="Thanks")
        self.assertEqual(t.stage, "")                              # stage is for scripts only
        self.client.post(reverse("control:crm_template_edit", kwargs={"pk": t.pk}),
                         {"kind": "whatsapp", "title": "Thanks!", "body": "Edited", "order": "6"})
        t.refresh_from_db()
        self.assertEqual((t.title, t.body, t.is_active), ("Thanks!", "Edited", False))
        self.login(self.a)
        self.assertEqual(self.client.post(reverse("control:crm_template_delete", kwargs={"pk": t.pk})).status_code, 403)
        self.login(self.admin)
        self.client.post(reverse("control:crm_template_delete", kwargs={"pk": t.pk}))
        self.assertFalse(MessageTemplate.objects.filter(pk=t.pk).exists())

    def test_invalid_template_rerenders_with_errors(self):
        self.login(self.admin)
        r = self.client.post(reverse("control:crm_template_new"), {"kind": "whatsapp", "title": "", "body": ""})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(MessageTemplate.objects.filter(title="").exists())

    def test_board_links_to_templates_for_admin(self):
        self.login(self.admin)
        self.assertIn(reverse("control:crm_templates"), self.client.get(reverse("control:crm_board")).content.decode())


class DemoSchedulingTests(CrmBase):
    def setUp(self):
        super().setUp()
        self.lead = Lead.objects.create(name="Zed", phone="9213529044", assigned_to=self.a, status=LeadStatus.INTERESTED)
        self.login(self.a)
        self.url = reverse("control:crm_lead_demo", kwargs={"pk": self.lead.pk})

    def test_booking_stores_india_time_and_moves_the_stage_with_history(self):
        r = self.client.post(self.url, {"when": "2026-10-07T16:30"})
        self.assertEqual(r.status_code, 302)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.demo_at, dt.datetime(2026, 10, 7, 11, 0, tzinfo=dt.timezone.utc))   # 16:30 IST
        self.assertEqual(self.lead.status, LeadStatus.DEMO_BOOKED)
        self.assertTrue(Activity.objects.filter(lead=self.lead, kind=ActivityKind.STAGE, note__contains="demo scheduled").exists())

    def test_rescheduling_keeps_stage_and_clearing_removes_time(self):
        self.client.post(self.url, {"when": "2026-10-07T16:30"})
        n = Activity.objects.filter(kind=ActivityKind.STAGE).count()
        self.client.post(self.url, {"when": "2026-10-08T10:00"})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.demo_at.astimezone(svc._biz_tz()).hour, 10)
        self.assertEqual(Activity.objects.filter(kind=ActivityKind.STAGE).count(), n)     # no second stage move
        self.client.post(self.url, {"when": ""})
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.demo_at)
        self.assertEqual(self.lead.status, LeadStatus.DEMO_BOOKED)

    def test_booking_never_moves_a_lead_that_is_further_along(self):
        Lead.objects.filter(pk=self.lead.pk).update(status=LeadStatus.NEGOTIATING)
        self.client.post(self.url, {"when": "2026-10-07T16:30"})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, LeadStatus.NEGOTIATING)

    def test_invalid_time_is_refused_and_foreign_lead_404s(self):
        self.client.post(self.url, {"when": "not a date"})
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.demo_at)
        other = Lead.objects.create(name="O", assigned_to=self.b)
        self.assertEqual(self.client.post(reverse("control:crm_lead_demo", kwargs={"pk": other.pk}), {"when": "2026-10-07T10:00"}).status_code, 404)

    def test_demos_for_splits_today_overdue_and_respects_the_india_day(self):
        tz = svc._biz_tz()
        now = timezone.now().astimezone(tz)
        sod = dt.datetime.combine(now.date(), dt.time.min, tzinfo=tz)
        today_l = Lead.objects.create(name="Today", assigned_to=self.a, status="demo_booked", demo_at=sod + dt.timedelta(hours=15))
        edge = Lead.objects.create(name="Edge", assigned_to=self.a, status="demo_booked", demo_at=sod + dt.timedelta(minutes=5))
        late = Lead.objects.create(name="Late", assigned_to=self.a, status="demo_booked", demo_at=sod - dt.timedelta(minutes=5))
        tomorrow = Lead.objects.create(name="Tomorrow", assigned_to=self.a, status="demo_booked", demo_at=sod + dt.timedelta(days=1, hours=1))
        done = Lead.objects.create(name="Done", assigned_to=self.a, status="demo_done", demo_at=sod + dt.timedelta(hours=9))
        theirs = Lead.objects.create(name="Theirs", assigned_to=self.b, status="demo_booked", demo_at=sod + dt.timedelta(hours=9))
        arch = Lead.objects.create(name="Arch", assigned_to=self.a, status="demo_booked", demo_at=sod + dt.timedelta(hours=9), is_archived=True)
        today, overdue = svc.demos_for(self.a)
        self.assertEqual([l.name for l in today], ["Edge", "Today"])        # time order
        self.assertEqual([l.name for l in overdue], ["Late"])

    def test_my_day_lists_todays_demos_and_overdue_ones(self):
        tz = svc._biz_tz()
        sod = dt.datetime.combine(timezone.now().astimezone(tz).date(), dt.time.min, tzinfo=tz)
        Lead.objects.create(name="Alpha Demo", business="Alpha Co", assigned_to=self.a, status="demo_booked", demo_at=sod + dt.timedelta(hours=16, minutes=30))
        Lead.objects.create(name="Beta Missed", assigned_to=self.a, status="demo_booked", demo_at=sod - dt.timedelta(days=2))
        html = self.client.get(reverse("control:crm_my_day")).content.decode()
        self.assertIn("Alpha Demo", html)
        self.assertIn("04:30 pm", html)
        self.assertIn("1 today", html)
        self.assertIn("Beta Missed", html)
        self.assertIn("mark demo given or reschedule", html)

    def test_lead_page_shows_demo_form_and_current_booking(self):
        html = self.client.get(reverse("control:crm_lead", kwargs={"pk": self.lead.pk})).content.decode()
        self.assertIn("Book a demo", html)
        self.client.post(self.url, {"when": "2026-10-07T16:30"})
        html = self.client.get(reverse("control:crm_lead", kwargs={"pk": self.lead.pk})).content.decode()
        self.assertIn('value="2026-10-07T16:30"', html)
        self.assertIn("Wed 07 Oct, 04:30 PM", html)
        self.assertIn("Reschedule", html)

    def test_board_card_shows_demo_badge_in_india_date(self):
        Lead.objects.filter(pk=self.lead.pk).update(status="demo_booked", demo_at=dt.datetime(2026, 10, 7, 20, 0, tzinfo=dt.timezone.utc))  # 01:30 IST on the 8th
        html = self.client.get(reverse("control:crm_lead_board")).content.decode()
        self.assertIn("🖥 08 Oct", html)


class MomentumTests(CrmBase):
    def _calls(self, user, day, n, outcome="connected", hour=11):
        tz = svc._biz_tz()
        when = dt.datetime.combine(day, dt.time(hour, 0), tzinfo=tz)
        Activity.objects.bulk_create([Activity(actor=user, kind="call", outcome=outcome, occurred_at=when + dt.timedelta(minutes=i)) for i in range(n)])

    def _today(self):
        return timezone.now().astimezone(svc._biz_tz()).date()

    def test_streak_counts_consecutive_active_days_and_today_is_forgiving(self):
        today = self._today()
        for back in (1, 2, 3):
            self._calls(self.a, today - dt.timedelta(days=back), 12)
        self.assertEqual(svc.call_streak(self.a)["days"], 3)               # today not done yet: streak intact
        self._calls(self.a, today, 11)
        self.assertEqual(svc.call_streak(self.a)["days"], 4)               # today now counts

    def test_a_quiet_day_breaks_the_streak(self):
        today = self._today()
        self._calls(self.a, today - dt.timedelta(days=1), 12)
        self._calls(self.a, today - dt.timedelta(days=2), 3)               # below the bar
        self._calls(self.a, today - dt.timedelta(days=3), 12)
        self.assertEqual(svc.call_streak(self.a)["days"], 1)

    def test_streak_bar_follows_a_low_target(self):
        from apps.crm.models import DailyTarget
        DailyTarget.objects.create(user=self.a, metric="calls", value=5)
        today = self._today()
        self._calls(self.a, today - dt.timedelta(days=1), 5)
        r = svc.call_streak(self.a)
        self.assertEqual((r["days"], r["bar"]), (1, 5))

    def test_streak_only_counts_the_users_own_calls(self):
        today = self._today()
        self._calls(self.b, today - dt.timedelta(days=1), 30)
        self.assertEqual(svc.call_streak(self.a)["days"], 0)

    def test_best_window_needs_enough_data_then_picks_the_best_two_hours(self):
        today = self._today()
        self._calls(self.a, today - dt.timedelta(days=1), 10, "no_answer", hour=9)
        self.assertIsNone(svc.best_call_window(self.a))                    # < 30 calls: say nothing
        self._calls(self.a, today - dt.timedelta(days=2), 20, "connected", hour=11)   # 100% in 10-12
        self._calls(self.a, today - dt.timedelta(days=3), 10, "connected", hour=15)
        self._calls(self.a, today - dt.timedelta(days=3), 10, "no_answer", hour=15)  # 50% in 14-16
        w = svc.best_call_window(self.a)
        self.assertEqual((w["label"], w["pct"]), ("10 am–12 pm", 100))

    def test_best_window_ignores_tiny_buckets(self):
        today = self._today()
        self._calls(self.a, today - dt.timedelta(days=1), 30, "no_answer", hour=14)
        self._calls(self.a, today - dt.timedelta(days=2), 3, "connected", hour=8)    # only 3 calls: not trusted
        self.assertIsNone(svc.best_call_window(self.a))

    def test_earnings_preview_uses_real_commission_and_weighted_pipeline(self):
        p = Project.objects.create(name="Paid Store", status="active")
        sub = billing.ensure_subscription(Project.objects.get(pk=p.pk))
        sub.manager = self.a
        sub.save(update_fields=["manager"])
        billing.mark_invoice_paid(billing.issue_invoice(sub))
        real = ManagerCommission.objects.get(manager=self.a)
        Lead.objects.create(name="n1", assigned_to=self.a, status=LeadStatus.NEGOTIATING)
        Lead.objects.create(name="n2", assigned_to=self.a, status=LeadStatus.NEGOTIATING)
        Lead.objects.create(name="d1", assigned_to=self.a, status=LeadStatus.DEMO_DONE)
        Lead.objects.create(name="x", assigned_to=self.a, status=LeadStatus.NEW, is_archived=True)   # ignored
        Lead.objects.create(name="o", assigned_to=self.b, status=LeadStatus.NEGOTIATING)             # someone else's
        e = svc.earnings_preview(self.a)
        self.assertEqual(e["earned"], real.amount.quantize(Decimal("1")))
        self.assertEqual(e["hot"], 3)
        cfg = CrmSettings.load()
        rate = svc._monthly_commission_pct() / Decimal(100)
        want = ((2 * Decimal(cfg.probability("negotiating")) + Decimal(cfg.probability("demo_done"))) / 100
                * cfg.avg_plan_price * rate).quantize(Decimal("1"))
        self.assertEqual(e["expected"], want)
        self.assertEqual(svc.earnings_preview(self.b)["earned"], 0)

    def test_my_day_shows_momentum_chips_only_with_data(self):
        self.login(self.a)
        html = self.client.get(reverse("control:crm_my_day")).content.decode()
        for needle in ("-day streak", "commission this month", "Pipeline could add", "You connect best"):
            self.assertNotIn(needle, html)
        today = self._today()
        for back in (1, 2):
            self._calls(self.a, today - dt.timedelta(days=back), 40, "connected", hour=11)
        Lead.objects.create(name="hot", assigned_to=self.a, status=LeadStatus.NEGOTIATING)
        html = self.client.get(reverse("control:crm_my_day")).content.decode()
        self.assertIn("2-day streak", html)
        self.assertIn("Pipeline could add", html)
        self.assertIn("1 hot", html)
        self.assertIn("You connect best 10 am–12 pm", html)


class OnboardingTests(CrmBase):
    def test_steps_tick_from_real_activity(self):
        steps = lambda: {s["label"]: s["done"] for s in svc.onboarding_steps(self.a)}  # noqa: E731
        self.assertFalse(any(steps().values()))
        svc.log_activity(actor=self.a, kind="call", outcome="no_answer")
        self.assertTrue(steps()["Log your first call"])
        self.assertFalse(steps()["Reach 10 calls"])
        for _ in range(9):
            svc.log_activity(actor=self.a, kind="call", outcome="no_answer")
        self.assertTrue(steps()["Reach 10 calls"])
        svc.log_activity(actor=self.a, kind="demo", outcome="done")
        self.assertTrue(steps()["Give your first demo"])
        TrainingLog.objects.create(trainee=self.a, trainer=self.b, status=TrainingStatus.CONFIRMED, initiated_by=self.a)
        self.assertTrue(steps()["Get trained by another DGC"])
        Lead.objects.create(name="W", assigned_to=self.a, status=LeadStatus.WON)
        self.assertTrue(all(steps().values()))

    def test_pending_training_or_someone_elses_wins_do_not_count(self):
        TrainingLog.objects.create(trainee=self.a, trainer=self.b, status=TrainingStatus.PENDING, initiated_by=self.a)
        Lead.objects.create(name="W", assigned_to=self.b, status=LeadStatus.WON)
        done = {s["label"]: s["done"] for s in svc.onboarding_steps(self.a)}
        self.assertFalse(done["Get trained by another DGC"] or done["Win your first store"])

    def test_card_shows_progress_until_everything_is_done(self):
        self.login(self.a)
        html = self.client.get(reverse("control:crm_my_day")).content.decode()
        self.assertIn("Getting started", html)
        self.assertIn("0 of 5 done", html)
        for _ in range(10):
            svc.log_activity(actor=self.a, kind="call", outcome="no_answer")
        svc.log_activity(actor=self.a, kind="demo", outcome="done")
        TrainingLog.objects.create(trainee=self.a, trainer=self.b, status=TrainingStatus.CONFIRMED, initiated_by=self.a)
        Lead.objects.create(name="W", assigned_to=self.a, status=LeadStatus.WON)
        self.assertNotIn("Getting started", self.client.get(reverse("control:crm_my_day")).content.decode())


class OfflineTimestampTests(CrmBase):
    def setUp(self):
        super().setUp()
        self.lead = Lead.objects.create(name="L", assigned_to=self.a)
        self.login(self.a)
        self.url = reverse("control:crm_log")

    def _post(self, at):
        return self.client.post(self.url, {"kind": "call", "outcome": "connected", "lead": self.lead.pk, "at": at},
                                HTTP_HX_REQUEST="true")

    def test_recent_timestamp_is_honoured_for_activity_and_first_touch(self):
        when = timezone.now() - dt.timedelta(hours=5)
        self.assertEqual(self._post(when.isoformat().replace("+00:00", "Z")).status_code, 204)
        act = Activity.objects.get(lead=self.lead)
        self.assertAlmostEqual(act.occurred_at.timestamp(), when.timestamp(), delta=2)
        self.lead.refresh_from_db()
        self.assertAlmostEqual(self.lead.first_touch_at.timestamp(), when.timestamp(), delta=2)

    def test_too_old_future_or_garbage_timestamps_fall_back_to_now(self):
        before = timezone.now()
        for bad in ((timezone.now() - dt.timedelta(days=4)).isoformat(), (timezone.now() + dt.timedelta(hours=2)).isoformat(), "garbage", ""):
            self._post(bad)
        for a in Activity.objects.all():
            self.assertGreaterEqual(a.occurred_at, before - dt.timedelta(seconds=1))
        self.assertEqual(Activity.objects.count(), 4)

    def test_naive_timestamp_is_treated_as_utc(self):
        when = (timezone.now() - dt.timedelta(hours=1)).replace(tzinfo=None)
        self._post(when.isoformat())
        self.assertAlmostEqual(Activity.objects.get().occurred_at.timestamp(),
                               when.replace(tzinfo=dt.timezone.utc).timestamp(), delta=2)

    def test_offline_script_is_in_the_shell_with_the_queue_wiring(self):
        html = self.client.get(reverse("control:crm_my_day")).content.decode()
        for needle in ("crmLogQueue", "flushQueue", "No signal", "Synced "):
            self.assertIn(needle, html)


class SourceSpendTests(CrmBase):
    def test_month_is_normalised_and_unique_per_source(self):
        s = SourceSpend.objects.create(source="Facebook", month=dt.date(2026, 10, 17), amount=Decimal("5000"))
        self.assertEqual(s.month, dt.date(2026, 10, 1))
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceSpend.objects.create(source="Facebook", month=dt.date(2026, 10, 2), amount=Decimal("1"))
