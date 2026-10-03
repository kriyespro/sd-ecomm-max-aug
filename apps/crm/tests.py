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
        self.assertNotIn("<details", html)  # edit form is always open now
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
        self.assertContains(self.client.get(reverse("control:crm_board")), "Assign a trainer to 1 request")

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
