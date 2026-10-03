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

    def test_sample_is_admin_only_and_linked(self):
        url = reverse("control:crm_lead_import_sample")
        self.login(self.a)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.login(self.admin)
        html = self.client.get(reverse("control:crm_leads") + "?view=list").content.decode()
        self.assertIn(url, html)
        self.assertIn("Download sample CSV", html)
