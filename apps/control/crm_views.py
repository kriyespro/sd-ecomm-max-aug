"""CRM + daily reporting (platform team).

* ``/admin/crm/`` — super-admin "Today" board: one glance at calls, demos,
  trained DGCs, collection, per person.
* ``/admin/crm/my-day/`` — a DGC's own numbers, leads, tasks.
* Leads, tasks, collections, DGC training log, store-work requests.

Platform admins see and assign everything; a DGC sees only their own rows.
"""

import csv
import datetime as dt
import io
import json
from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Case, Count, F, IntegerField, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView, TemplateView, View

from apps.accounts.permissions import (
    OWNER_MANAGER,
    StoreRoleRequiredMixin,
    is_platform_admin,
    managed_projects,
)
from apps.core.mixins import PlatformAdminRequiredMixin, PlatformStaffRequiredMixin
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.crm import services as svc
from apps.crm.tz import biz_today, to_biz
from apps.crm.models import (
    Activity,
    ActivityKind,
    ActivityOutcome,
    Collection,
    CollectionMode,
    CrmProfile,
    CrmSettings,
    CollectionStatus,
    DailyTarget,
    Lead,
    LeadStatus,
    MessageTemplate,
    SourceSpend,
    STAGE_RANK,
    STAGE_ROW_TONE,
    TemplateKind,
    StoreAssignment,
    StoreWorkRequest,
    TargetMetric,
    Task,
    TaskStatus,
    TrainingLog,
    TrainingStatus,
    WorkKind,
    WorkRequestStatus,
)

from .mixins import ActiveProjectMixin

User = get_user_model()

_RANGES = [("today", "Today"), ("yesterday", "Yesterday"), ("7d", "7 days"), ("month", "Month")]


def _admin(user):
    return is_platform_admin(user)


def _post_next(request, default):
    nxt = request.POST.get("next") or ""
    return nxt if nxt.startswith("/admin/") else default


# ───────────────────────────── board / my day ─────────────────────────────

class CrmBoardView(PlatformAdminRequiredMixin, TemplateView):
    template_name = "control/crm/board.jinja"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        key = self.request.GET.get("range", "today")
        start, end, label = svc.resolve_range(key)
        rows, totals = svc.person_numbers(start, end)
        # Default view hides people with no activity; ?active=0 shows everyone.
        active_only = self.request.GET.get("active", "1") != "0"
        if active_only:
            rows = [r for r in rows if any((
                r["calls"], r["demos"], r["trained"], r["collection"], r["collection_pending"],
                r["products"], r["store_work"], r["whatsapp"], r["trained_others"]))]
        ctx.update(inbox=svc.admin_inbox(), pipeline=svc.pipeline_counts(), dgcs=svc.dgc_users(),
                   people=svc.crm_people())
        ctx.update(range_key=key, range_label=label, ranges=_RANGES, rows=rows, active_only=active_only,
                   totals=totals, extras=svc.board_extras(), start=start, end=end)
        return ctx


class CrmHomeView(PlatformStaffRequiredMixin, View):
    """/admin/crm/ — where the CRM opens. A platform admin gets the team board; a
    DGC lands on their Welcome page (the coaching view comes first)."""

    def get(self, request, *args, **kwargs):
        if _admin(request.user):
            return CrmBoardView.as_view()(request, *args, **kwargs)
        return redirect("control:crm_welcome")


class WelcomeView(PlatformStaffRequiredMixin, TemplateView):
    """The DGC's welcome page: what their calls can become, at THEIR percentages
    (set by an admin), on the DGC price. An admin can preview any DGC's page with
    ?dgc=<id>."""

    template_name = "control/crm/welcome.jinja"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        me = self.request.user
        preview = None
        raw = self.request.GET.get("dgc", "")
        if _admin(me) and raw.isdigit():
            preview = get_object_or_404(svc.dgc_users(), pk=int(raw))
        who = preview or me
        ctx.update(possible=svc.possibility(who), person=who, preview=preview,
                   first_name=who.first_name or (who.email.split("@")[0] if who.email else "there"))
        return ctx


class MyDayView(PlatformStaffRequiredMixin, TemplateView):
    template_name = "control/crm/my_day.jinja"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        u = self.request.user
        key = self.request.GET.get("range", "today")
        start, end, label = svc.resolve_range(key)
        rows, totals = svc.person_numbers(start, end, users=[u])
        today = biz_today()
        skip = [int(i) for i in self.request.GET.get("skip", "").split(",")[:50] if i.isdigit()]
        fresh_cut = timezone.now() - dt.timedelta(hours=svc.FRESH_HOURS)
        fresh = list(Lead.objects.filter(
            assigned_to=u, is_archived=False, first_touch_at__isnull=True, assigned_at__gte=fresh_cut,
            status__in=[s_ for s_ in LeadStatus.values if s_ not in ("won", "lost")]).order_by("assigned_at")[:5])
        for f in fresh:
            f.waiting_mins = max(0, int((timezone.now() - f.assigned_at).total_seconds() // 60))
        demos_today, demos_overdue = svc.demos_for(u)
        steps = svc.onboarding_steps(u)
        queue = svc.call_queue(u, skip=skip)
        nxt = queue.first()
        ctx.update(
            demos_today=demos_today, demos_overdue=demos_overdue,
            earnings=svc.earnings_preview(u), streak=svc.call_streak(u), best_window=svc.best_call_window(u),
            possible=svc.possibility(u),
            onboarding=steps, onboarding_done=sum(1 for st in steps if st["done"]),
            wa_links=svc.whatsapp_links(queue.first(), u) if queue.first() else [],
            nxt=nxt, queue_left=queue.count(), skip_ids=skip, fresh=fresh,
            crm_profile=CrmProfile.objects.filter(user=u).first(),
            nxt_activity=nxt.activities.select_related("actor")[:3] if nxt else [],
            skip_param=",".join(map(str, skip + ([nxt.pk] if nxt else []))),
            today_calls=rows[0]["calls"] if key == "today" else Activity.objects.filter(
                actor=u, kind=ActivityKind.CALL, occurred_at__gte=svc.day_bounds(today)[0]).count(),
            range_key=key, range_label=label, ranges=_RANGES, me=rows[0], targets=rows[0]["targets"],
            due_leads=Lead.objects.filter(assigned_to=u, is_archived=False, next_follow_up__lte=today,
                                          status__in=[s for s in LeadStatus.values
                                                      if s not in ("won", "lost")])[:20],
            open_tasks=Task.objects.filter(assignee=u, status=TaskStatus.OPEN)[:20],
            recent=Activity.objects.filter(actor=u).select_related("lead")[:15],
            my_work=StoreAssignment.objects.filter(assignee=u, is_active=True).select_related("project"),
            pending_confirm=svc.trainings_to_confirm(u).count(),
            waiting_to_train=TrainingLog.objects.filter(trainer=u, status=TrainingStatus.ASSIGNED).count(),
        )
        return ctx


# ───────────────────────────── leads ─────────────────────────────

class LeadForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = ["name", "phone", "business", "city", "source", "status", "next_follow_up", "notes"]
        widgets = {"next_follow_up": forms.DateInput(attrs={"type": "date"}),
                   "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        # Quick-add (board, imports) only sends a name + phone; omitted status
        # falls back to the model default ("new").
        self.fields["status"].required = False


def _lead_qs(user):
    qs = Lead.objects.select_related("assigned_to")
    return qs if _admin(user) else qs.filter(assigned_to=user)


LEADS_VIEW_COOKIE = "crm_leads_view"
LEADS_AUTO_COOKIE = "crm_leads_auto"


class LeadListView(PlatformStaffRequiredMixin, ListView):
    template_name = "control/crm/leads.jinja"
    context_object_name = "leads"
    paginate_by = 50

    def get(self, request, *args, **kwargs):
        # Board is the default; a plain /leads/ visit goes there unless the
        # user last chose the list (cookie) or is filtering / paging / asking
        # for the list explicitly.
        g = request.GET
        wants_list = (g.get("view") == "list" or request.COOKIES.get(LEADS_VIEW_COOKIE) == "list"
                      or any(g.get(k) for k in ("q", "status", "assignee", "due", "page", "archived")))
        if not wants_list:
            return redirect("control:crm_lead_board")
        resp = super().get(request, *args, **kwargs)
        if g.get("view") == "list":
            resp.set_cookie(LEADS_VIEW_COOKIE, "list", max_age=60 * 60 * 24 * 365, samesite="Lax")
        if "auto" in g:
            resp.set_cookie(LEADS_AUTO_COOKIE, "1" if g.get("auto") == "1" else "0",
                            max_age=60 * 60 * 24 * 365, samesite="Lax")
        return resp

    def _auto_mode(self):
        g = self.request.GET
        if "auto" in g:
            return g.get("auto") == "1"
        return self.request.COOKIES.get(LEADS_AUTO_COOKIE) == "1"

    def get_queryset(self):
        g = self.request.GET
        qs = _lead_qs(self.request.user).filter(is_archived=bool(g.get("archived")))
        if g.get("status") in LeadStatus.values:
            qs = qs.filter(status=g["status"])
        if g.get("assignee") == "none":
            qs = qs.filter(assigned_to__isnull=True)
        elif g.get("assignee", "").isdigit() and _admin(self.request.user):
            qs = qs.filter(assigned_to_id=int(g["assignee"]))
        if g.get("due"):
            qs = qs.filter(next_follow_up__lte=biz_today())
        if g.get("q"):
            q = g["q"].strip()
            qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(business__icontains=q))
        if self._auto_mode():
            # Auto ON: hottest / most-converted first (Won at the very top),
            # then whoever's follow-up is soonest due, then the newest. A
            # single-status filter makes the rank tie for everyone, so it
            # quietly falls through to the follow-up/recency order within
            # that one status. Lost is sunk to the bottom via its own lowest
            # rank in STAGE_RANK.
            rank = Case(*(When(status=st, then=Value(r)) for st, r in STAGE_RANK.items()), output_field=IntegerField())
            return qs.annotate(_rank=rank).order_by("-_rank", F("next_follow_up").asc(nulls_last=True), "-created_at")
        # Auto OFF (the default): plain newest-first, the order a DGC has
        # always worked in, so a fresh lead never needs scrolling to find.
        # The one thing kept from Auto either way: Lost sinks to the bottom
        # instead of cluttering the middle of the list.
        return qs.order_by(Case(When(status=LeadStatus.LOST, then=Value(1)), default=Value(0),
                                output_field=IntegerField()), "-created_at")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        g = self.request.GET.copy()
        g.pop("page", None)
        auto_base = g.copy()
        auto_base.pop("auto", None)
        auto_mode = self._auto_mode()
        ctx.update(statuses=LeadStatus.choices, is_admin=_admin(self.request.user),
                   # Inline status edit: everything except "won" (that has its own store flow)
                   editable_statuses=[c for c in LeadStatus.choices if c[0] != LeadStatus.WON],
                   people=svc.crm_people(), g=self.request.GET, form=LeadForm(),
                   today=biz_today(), qs=(g.urlencode() + "&") if g else "",
                   archived_view=bool(self.request.GET.get("archived")),
                   dgcs=svc.dgc_users() if _admin(self.request.user) else [],
                   unassigned_count=Lead.objects.filter(
                       assigned_to__isnull=True, is_archived=False,
                       status__in=[s for s in LeadStatus.values if s not in ("won", "lost")]).count()
                   if _admin(self.request.user) else 0,
                   # "Auto": off by default — see _auto_mode. ON reorders hottest/Won
                   # first and shades rows by stage; OFF keeps the plain newest-first
                   # order everyone is used to (only Lost still sinks to the bottom).
                   # Row shading + the matching JSON the JS recolor-on-change handler
                   # reads both come from one dict — apps.crm.models.STAGE_ROW_TONE.
                   auto_mode=auto_mode, auto_toggle_url="?" + auto_base.urlencode() + ("&" if auto_base else "") + "auto=",
                   stage_tone=STAGE_ROW_TONE, stage_tone_json=json.dumps(STAGE_ROW_TONE))
        return ctx


class LeadCreateView(PlatformStaffRequiredMixin, View):
    """Add one lead. A phone number alone is enough (the number becomes the
    name until someone edits it) — quick-add from My day / the board. A phone
    already in the CRM is refused so two DGCs never chase the same person."""

    def post(self, request):
        data = request.POST.copy()
        phone = data.get("phone", "").strip()
        if not data.get("name", "").strip() and phone:
            data["name"] = phone
        back = _post_next(request, reverse("control:crm_leads"))
        form = LeadForm(data)
        if not form.is_valid():
            messages.error(request, "Add a name or a phone number.")
            return redirect(back)
        if phone and svc.phone_exists(phone):
            messages.warning(request, "That number is already in the CRM — nothing added.")
            return redirect(back)
        lead = form.save(commit=False)
        lead.created_by = request.user
        if not _admin(request.user) or not request.POST.get("assigned_to"):
            lead.assigned_to = request.user
        else:
            lead.assigned_to = User.objects.filter(pk=request.POST["assigned_to"]).first()
        lead.assigned_by = request.user
        lead.save()
        messages.success(request, f"Added {lead.name}.")
        return redirect(_post_next(request, reverse("control:crm_lead", kwargs={"pk": lead.pk})))


class LeadDetailView(PlatformStaffRequiredMixin, TemplateView):
    template_name = "control/crm/lead.jinja"

    def get_context_data(self, pk, **kw):
        ctx = super().get_context_data(**kw)
        lead = get_object_or_404(_lead_qs(self.request.user), pk=pk)
        demo_local = lead.demo_at.astimezone(svc._biz_tz()) if lead.demo_at else None
        ctx.update(wa_links=svc.whatsapp_links(lead, self.request.user),
                   scripts=svc.scripts_for(lead, self.request.user),
                   demo_value=demo_local.strftime("%Y-%m-%dT%H:%M") if demo_local else "",
                   demo_label=demo_local.strftime("%a %d %b, %I:%M %p") if demo_local else "")
        ctx.update(lead=lead, form=LeadForm(instance=lead),
                   activities=lead.activities.select_related("actor")[:50],
                   tasks=lead.tasks.filter(status=TaskStatus.OPEN),
                   outcomes=ActivityOutcome.choices, is_admin=_admin(self.request.user),
                   people=svc.crm_people())
        return ctx

    def post(self, request, pk):  # edit
        lead = get_object_or_404(_lead_qs(request.user), pk=pk)
        form = LeadForm(request.POST, instance=lead)
        if form.is_valid():
            form.save()
            messages.success(request, "Lead updated.")
        else:
            messages.error(request, "Fix the form errors.")
        return redirect("control:crm_lead", pk=pk)


class LeadBoardView(PlatformStaffRequiredMixin, TemplateView):
    """Kanban of the pipeline. Staff see their own leads; admins can filter
    by owner. Each column is capped (``?full=<stage>`` lifts it)."""

    template_name = "control/crm/leads_board.jinja"

    def get(self, request, *args, **kwargs):
        resp = super().get(request, *args, **kwargs)
        resp.set_cookie(LEADS_VIEW_COOKIE, "board", max_age=60 * 60 * 24 * 365, samesite="Lax")
        return resp

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        g = self.request.GET
        admin = _admin(self.request.user)
        data = svc.board_columns(
            self.request.user, owner=g.get("owner") or None, q=(g.get("q") or "").strip(),
            full=g.get("full"), admin=admin)
        ctx.update(data, total=sum(c["count"] for c in data["columns"]), is_admin=admin, g=g, people=svc.crm_people() if admin else [],
                   statuses=[(s.value, s.label) for s in svc.BOARD_STAGES] + [("lost", "Lost")],
                   limit=svc.BOARD_LIMIT)
        return ctx


class LeadMoveView(PlatformStaffRequiredMixin, View):
    """Board drag / menu move. JSON in spirit: 204 ok, 400/409 with a message."""

    def post(self, request, pk):
        from django.http import HttpResponse, JsonResponse

        lead = get_object_or_404(_lead_qs(request.user), pk=pk)
        try:
            svc.move_lead(lead, request.POST.get("status", ""), actor=request.user,
                          reason=request.POST.get("reason", ""))
        except svc.MoveError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=409)
        return HttpResponse(status=204)


class LeadStatusView(PlatformStaffRequiredMixin, View):
    """One-tap pipeline move from the lead page stepper."""

    def post(self, request, pk):
        lead = get_object_or_404(_lead_qs(request.user), pk=pk)
        try:
            svc.move_lead(lead, request.POST.get("status", ""), actor=request.user)
        except svc.MoveError:
            pass  # "won" has its own button; unknown stages are ignored
        return redirect("control:crm_lead", pk=pk)


class LeadAssignView(PlatformAdminRequiredMixin, View):
    """Bulk assign: POST ids[] + assignee."""

    def post(self, request):
        ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
        who = User.objects.filter(pk=request.POST.get("assignee") or 0).first()
        if not ids or who is None:
            messages.error(request, "Tick leads and choose who gets them.")
        else:
            n = svc.assign_leads(Lead.objects.filter(pk__in=ids), who, actor=request.user)
            messages.success(request, f"{n} lead(s) assigned to {svc.person_label(who)}.")
        return redirect(_post_next(request, reverse("control:crm_leads")))


class LeadDistributeView(PlatformAdminRequiredMixin, View):
    """Spread leads evenly over DGCs. With ``ids`` -> those leads; without ->
    every unassigned open lead. ``assignees`` defaults to all DGCs."""

    UNASSIGNED_CAP = 2000

    def post(self, request):
        ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
        picked = [int(i) for i in request.POST.getlist("assignees") if i.isdigit()]
        people = svc.dgc_users()
        if picked:
            people = people.filter(pk__in=picked)
        people = list(people)
        if not people:
            messages.error(request, "Choose at least one DGC to share the leads between.")
            return redirect(_post_next(request, reverse("control:crm_leads")))
        if ids:
            if len(ids) > svc.BULK_ARCHIVE_MAX:
                messages.error(request, f"Pick at most {svc.BULK_ARCHIVE_MAX} leads at a time.")
                return redirect(_post_next(request, reverse("control:crm_leads")))
            leads = Lead.objects.filter(pk__in=ids, is_archived=False)
        else:
            leads = Lead.objects.filter(assigned_to__isnull=True, is_archived=False,
                                        status__in=[s for s in LeadStatus.values if s not in ("won", "lost")]
                                        ).order_by("created_at")[:self.UNASSIGNED_CAP]
        got = svc.distribute_leads(leads, people, actor=request.user)
        total = sum(got.values())
        if not total:
            messages.info(request, "No leads to share out.")
        else:
            who = {u.pk: svc.person_label(u) for u in people}
            detail = ", ".join(f"{who[pk]} {n}" for pk, n in got.items() if n)
            messages.success(request, f"Shared {total} lead(s) evenly: {detail}.")
        return redirect(_post_next(request, reverse("control:crm_leads")))


class PersonView(PlatformAdminRequiredMixin, TemplateView):
    """One DGC at a glance: today's numbers, their pipeline, how long since
    they did anything, and the levers an admin needs (task, transfer leads)."""

    template_name = "control/crm/person.jinja"

    def get_context_data(self, pk, **kw):
        ctx = super().get_context_data(**kw)
        person = get_object_or_404(svc.crm_people(), pk=pk)
        key = self.request.GET.get("range", "today")
        start, end, label = svc.resolve_range(key)
        rows, _ = svc.person_numbers(start, end, users=[person])
        today = biz_today()
        mine = Lead.objects.filter(assigned_to=person, is_archived=False)
        stage = {r["status"]: r["n"] for r in mine.values("status").annotate(n=Count("id"))}
        open_n = sum(stage.get(s.value, 0) for s in svc.BOARD_STAGES)
        ctx.update(
            person=person, me=rows[0], range_key=key, range_label=label, ranges=_RANGES,
            stages=[(s.value, s.label, stage.get(s.value, 0)) for s in svc.BOARD_STAGES],
            won=stage.get("won", 0), lost=stage.get("lost", 0), open_total=open_n,
            overdue=mine.filter(next_follow_up__lt=today, status__in=[s.value for s in svc.BOARD_STAGES]).count(),
            recent=Activity.objects.filter(actor=person).select_related("lead")[:15],
            open_tasks=Task.objects.filter(assignee=person, status=TaskStatus.OPEN)[:6],
            others=svc.crm_people().exclude(pk=person.pk), targets=rows[0]["targets"],
            training_waiting=TrainingLog.objects.filter(trainer=person, status=TrainingStatus.ASSIGNED).count(),
            profile=CrmProfile.objects.filter(user=person).first(), team=CrmSettings.load(),
            eff=svc.funnel_for(person), cv=svc.client_value(person),
        )
        return ctx


class PersonFunnelView(PlatformAdminRequiredMixin, View):
    """A platform admin sets one DGC's conversion percentages and commission %.
    A blank box means "use the team default". All-or-nothing: one bad value
    changes nothing."""

    FIELDS = ("conv_call_to_demo", "conv_demo_to_trial", "conv_trial_to_paid", "commission_pct")

    def post(self, request, pk):
        person = get_object_or_404(svc.crm_people(), pk=pk)
        back = reverse("control:crm_person", kwargs={"pk": pk})
        values = {}
        for name in self.FIELDS:
            raw = (request.POST.get(name) or "").strip().rstrip("%").strip()
            if not raw:
                values[name] = None
                continue
            if not raw.isdigit() or not 1 <= int(raw) <= 100:
                messages.error(request, "Each percentage must be a whole number from 1 to 100 (or blank for the team default).")
                return redirect(back)
            values[name] = int(raw)
        prof, _ = CrmProfile.objects.get_or_create(user=person)
        for name, v in values.items():
            setattr(prof, name, v)
        prof.save()
        record_audit(actor=request.user, action=AuditLog.Action.UPDATE, target=prof,
                     changes={"crm_dgc_funnel": {k: v for k, v in values.items()}, "dgc": person.pk})
        messages.success(request, f"Saved the percentages for {svc.person_label(person)}.")
        return redirect(back)


class LeadTransferView(PlatformAdminRequiredMixin, View):
    """Move all of one DGC's open leads to someone else, or spread them evenly
    over every other DGC."""

    def post(self, request, pk):
        src = get_object_or_404(svc.crm_people(), pk=pk)
        back = reverse("control:crm_person", kwargs={"pk": pk})
        to = request.POST.get("to", "")
        if to == "spread":
            targets = list(svc.dgc_users().exclude(pk=src.pk))
        elif to.isdigit() and int(to) != src.pk:
            targets = list(svc.crm_people().filter(pk=int(to)))
        else:
            targets = []
        if not targets:
            messages.error(request, "Choose who should take over these leads.")
            return redirect(back)
        got = svc.transfer_leads(src, targets, actor=request.user)
        total = sum(got.values())
        messages.success(request, f"Moved {total} open lead(s) from {svc.person_label(src)}." if total
                         else f"{svc.person_label(src)} has no open leads to move.")
        return redirect(back)


class LeadBulkStatusView(PlatformStaffRequiredMixin, View):
    """Set one stage on every ticked lead. Same rules as a single move (won is
    refused, history written), scoped to the caller's own leads."""

    def post(self, request):
        ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
        status = request.POST.get("status", "")
        if not ids:
            messages.error(request, "Tick at least one lead first.")
        elif len(ids) > svc.BULK_ARCHIVE_MAX:
            messages.error(request, f"Pick at most {svc.BULK_ARCHIVE_MAX} leads at a time.")
        elif status not in LeadStatus.values or status == LeadStatus.WON:
            messages.error(request, "Choose a stage to move them to (use “Deal won” for won).")
        else:
            moved = 0
            for lead in _lead_qs(request.user).filter(pk__in=ids, is_archived=False):
                try:
                    if lead.status != status:
                        svc.move_lead(lead, status, actor=request.user)
                        moved += 1
                except svc.MoveError:
                    continue  # e.g. an already-won lead in the selection
            messages.success(request, f"Moved {moved} lead(s) to {LeadStatus(status).label}.")
        return redirect(_post_next(request, reverse("control:crm_leads")))


class LeadArchiveView(PlatformStaffRequiredMixin, View):
    """Bulk (or single) archive / restore. Scoped like everything else: a DGC
    only ever touches their own leads, whatever ids they send."""

    def post(self, request):
        from django.http import HttpResponse

        ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
        restore = request.POST.get("action") == "restore"
        if not ids:
            messages.error(request, "Tick at least one lead first.")
        elif len(ids) > svc.BULK_ARCHIVE_MAX:
            messages.error(request, f"Pick at most {svc.BULK_ARCHIVE_MAX} leads at a time.")
        else:
            n = svc.archive_leads(_lead_qs(request.user).filter(pk__in=ids),
                                  actor=request.user, restore=restore)
            if request.headers.get("HX-Request"):
                return HttpResponse(status=204)
            verb, hint = ("Restored", "") if restore else ("Archived", " — find them under “Archived” to restore.")
            messages.success(request, f"{verb} {n} lead(s){hint}")
        return redirect(_post_next(request, reverse("control:crm_leads")))


class LeadDemoView(PlatformStaffRequiredMixin, View):
    """Book (or clear) the demo time for a lead. Times are India time."""

    def post(self, request, pk):
        lead = get_object_or_404(_lead_qs(request.user), pk=pk)
        raw = request.POST.get("when", "").strip()
        if not raw:
            svc.book_demo(lead, None, actor=request.user)
            messages.success(request, "Demo time cleared.")
        else:
            try:
                when = dt.datetime.fromisoformat(raw).replace(tzinfo=svc._biz_tz())
            except ValueError:
                messages.error(request, "That date/time isn't valid.")
                return redirect("control:crm_lead", pk=pk)
            svc.book_demo(lead, when, actor=request.user)
            messages.success(request, f"Demo booked for {when.strftime('%a %d %b, %I:%M %p')}.")
        return redirect("control:crm_lead", pk=pk)


class LeadWonView(PlatformStaffRequiredMixin, View):
    """Mark won → hand to the existing store-create screen."""

    def post(self, request, pk):
        lead = get_object_or_404(_lead_qs(request.user), pk=pk)
        lead.status = LeadStatus.WON
        lead.save(update_fields=["status", "updated_at"])
        messages.success(request, "Marked won — create the store now.")
        return redirect("control:store_create")


class LeadImportSampleView(PlatformStaffRequiredMixin, View):
    """A ready-to-edit CSV in exactly the shape LeadImportView reads. UTF-8 with
    a BOM so Excel opens it with the right encoding."""

    ROWS = [
        ["name", "phone", "business", "city", "source"],
        ["Ramesh Sharma", "9876543210", "Sharma Jewellers", "Pune", "Cold call"],
        ["Anita Rao", "9123456780", "Rao Boutique", "Hyderabad", "Referral"],
        ["Imran Khan", "9988776655", "Khan Mobiles", "Jaipur", "Instagram ad"],
    ]

    def get(self, request):
        from django.http import HttpResponse

        buf = io.StringIO()
        csv.writer(buf).writerows(self.ROWS)
        resp = HttpResponse("\ufeff" + buf.getvalue(), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="leads-sample.csv"'
        return resp


class LeadImportView(PlatformStaffRequiredMixin, View):
    """CSV: name,phone,business,city,source (header row optional).

    Admins may assign the batch to anyone; a DGC's import always lands on
    their own list (the assignee field is ignored for them)."""

    def post(self, request):
        f = request.FILES.get("file")
        if not f or f.size > 2 * 1024 * 1024:
            messages.error(request, "Upload a CSV under 2 MB.")
            return redirect("control:crm_leads")
        try:
            text = f.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            messages.error(request, "CSV must be UTF-8.")
            return redirect("control:crm_leads")
        if _admin(request.user):
            who = User.objects.filter(pk=request.POST.get("assignee") or 0).first()
        else:
            who = request.user
        spread = _admin(request.user) and bool(request.POST.get("spread"))
        made = skipped = 0
        created = []
        for i, row in enumerate(csv.reader(io.StringIO(text))):
            row = [c.strip() for c in row] + [""] * 5
            if i == 0 and row[0].lower() in ("name", "lead", "full name"):
                continue
            name, phone, biz, city, src = row[:5]
            if not name or (phone and svc.phone_exists(phone)):
                skipped += 1
                continue
            if made >= 2000:
                break
            created.append(Lead.objects.create(
                name=name[:120], phone=phone[:20], business=biz[:160], city=city[:80],
                source=(src or "import")[:60], assigned_to=None if spread else who,
                assigned_by=request.user if (who and not spread) else None, created_by=request.user))
            made += 1
        note = ""
        if spread and created:
            got = svc.distribute_leads(created, svc.dgc_users(), actor=request.user)
            note = f" Split evenly across {sum(1 for n in got.values() if n)} DGC(s)." if got else \
                " No DGCs to split between — leads left unassigned."
        messages.success(request, f"Imported {made} lead(s); skipped {skipped} "
                                  f"(blank name or phone already in the CRM).{note}")
        return redirect("control:crm_leads")


# ───────────────────────────── quick log ─────────────────────────────

class LogActivityView(PlatformStaffRequiredMixin, View):
    """2-tap logger: kind + outcome (+ optional lead, note, follow-up date)."""

    def post(self, request):
        p = request.POST
        kind = p.get("kind")
        if not kind:  # form submitted without picking what happened
            messages.error(request, "Pick what happened first.")
            return redirect(_post_next(request, reverse("control:crm_my_day")))
        if kind not in ActivityKind.values:
            raise Http404
        outcome = p.get("outcome", "")
        if outcome and outcome not in ActivityOutcome.values:
            outcome = ""
        lead = None
        if p.get("lead", "").isdigit():
            lead = get_object_or_404(_lead_qs(request.user), pk=int(p["lead"]))
        project = None
        if p.get("project", "").isdigit():
            project = get_object_or_404(self._projects(request.user), pk=int(p["project"]))
        follow = None
        if p.get("follow_in", "").isdigit() and 0 < int(p["follow_in"]) <= 90:
            follow = biz_today() + dt.timedelta(days=int(p["follow_in"]))
        elif p.get("follow_up"):
            try:
                follow = dt.date.fromisoformat(p["follow_up"])
            except ValueError:
                pass
        count = p.get("count", "1")
        when = None
        if p.get("at"):   # sent by the offline queue: the moment the call actually ended
            try:
                parsed = dt.datetime.fromisoformat(p["at"].replace("Z", "+00:00"))
                if timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed, dt.timezone.utc)
                now = timezone.now()
                if now - dt.timedelta(days=3) <= parsed <= now + dt.timedelta(minutes=5):
                    when = parsed
            except ValueError:
                pass
        svc.log_activity(actor=request.user, kind=kind, outcome=outcome, lead=lead,
                         project=project, note=p.get("note", ""),
                         count=int(count) if count.isdigit() else 1, follow_up=follow,
                         occurred_at=when)
        if request.headers.get("HX-Request"):
            from django.http import HttpResponse
            return HttpResponse(status=204, headers={"HX-Refresh": "true"})
        messages.success(request, "Logged.")
        return redirect(_post_next(request, reverse("control:crm_my_day")))

    @staticmethod
    def _projects(user):
        from apps.projects.services import projects_for_user
        return projects_for_user(user)


# ───────────────────────────── tasks ─────────────────────────────

class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["title", "assignee", "due_on", "detail"]
        widgets = {"due_on": forms.DateInput(attrs={"type": "date"}),
                   "title": forms.TextInput(attrs={"placeholder": "e.g. Call 20 jewellers in Pune"}),
                   "detail": forms.Textarea(attrs={"rows": 1, "placeholder": "Details (optional)"})}
        labels = {"due_on": "Due", "assignee": "Give to"}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["assignee"].queryset = svc.crm_people()
        self.fields["assignee"].label_from_instance = svc.person_label
        self.fields["assignee"].empty_label = "Choose person…"
        self.fields["detail"].required = False


class TaskListView(PlatformStaffRequiredMixin, TemplateView):
    template_name = "control/crm/tasks.jinja"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        qs = Task.objects.select_related("assignee", "lead")
        admin = _admin(self.request.user)
        if not admin:
            qs = qs.filter(assignee=self.request.user)
        ctx.update(tasks=qs.filter(status=TaskStatus.OPEN)[:200],
                   done=qs.filter(status=TaskStatus.DONE).order_by("-done_at")[:20],
                   is_admin=admin, form=TaskForm() if admin else None)
        return ctx


class TaskCreateView(PlatformAdminRequiredMixin, View):
    def post(self, request):
        form = TaskForm(request.POST)
        if form.is_valid():
            t = form.save(commit=False)
            t.assigned_by = request.user
            t.save()
            messages.success(request, "Task assigned.")
        else:
            messages.error(request, "Task needs a title and an assignee.")
        return redirect(_post_next(request, reverse("control:crm_tasks")))


class TaskDoneView(PlatformStaffRequiredMixin, View):
    def post(self, request, pk):
        qs = Task.objects.all() if _admin(request.user) else Task.objects.filter(assignee=request.user)
        svc.mark_task_done(get_object_or_404(qs, pk=pk))
        return redirect(_post_next(request, reverse("control:crm_tasks")))


# ───────────────────────────── collections ─────────────────────────────

class CollectionForm(forms.ModelForm):
    class Meta:
        model = Collection
        fields = ["amount", "mode", "reference", "collected_on", "project", "proof", "note"]
        widgets = {"collected_on": forms.DateInput(attrs={"type": "date"}),
                   "note": forms.TextInput(attrs={"placeholder": "Note (optional)"}),
                   "reference": forms.TextInput(attrs={"placeholder": "UTR / receipt no."})}
        labels = {"collected_on": "Date", "project": "Store (optional)", "proof": "Proof (optional)",
                  "amount": "Amount ₹"}

    def __init__(self, *a, user=None, **kw):
        super().__init__(*a, **kw)
        self.fields["mode"].choices = [c for c in CollectionMode.choices if c[0] != "subscription"]
        self.fields["mode"].initial = CollectionMode.UPI
        self.fields["project"].empty_label = "— none —"
        self.fields["project"].required = False
        self.fields["project"].queryset = managed_projects(user) if user else self.fields["project"].queryset
        self.fields["reference"].required = True

    def clean_amount(self):
        a = self.cleaned_data["amount"]
        if a <= 0:
            raise forms.ValidationError("Amount must be above zero.")
        return a


class CollectionListView(PlatformStaffRequiredMixin, TemplateView):
    template_name = "control/crm/collections.jinja"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        admin = _admin(self.request.user)
        qs = Collection.objects.select_related("collected_by", "project")
        if not admin:
            qs = qs.filter(collected_by=self.request.user)
        st = self.request.GET.get("status")
        if st in CollectionStatus.values:
            qs = qs.filter(status=st)
        ctx.update(collections=qs[:200], is_admin=admin, g=self.request.GET,
                   form=CollectionForm(user=self.request.user), statuses=CollectionStatus.choices)
        return ctx


class CollectionCreateView(PlatformStaffRequiredMixin, View):
    def post(self, request):
        form = CollectionForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            c = form.save(commit=False)
            c.collected_by = request.user
            c.save()
            messages.success(request, "Entered — counts on the board once a platform admin verifies it.")
        else:
            messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
        return redirect("control:crm_collections")


class CollectionVerifyView(PlatformAdminRequiredMixin, View):
    def post(self, request, pk):
        c = get_object_or_404(Collection, pk=pk)
        if c.mode == CollectionMode.SUBSCRIPTION:
            raise PermissionDenied("Invoice collections are verified automatically.")
        svc.verify_collection(c, actor=request.user, ok=request.POST.get("action") != "reject")
        return redirect(_post_next(request, reverse("control:crm_collections")))


# ───────────────────────────── DGC training ─────────────────────────────

def _person(pk, *, exclude=None):
    qs = svc.crm_people()
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    return qs.filter(pk=pk).first() if str(pk or "").isdigit() else None


def _date_or_today(raw):
    try:
        return dt.date.fromisoformat(raw) if raw else biz_today()
    except ValueError:
        return biz_today()


class TrainingListView(PlatformStaffRequiredMixin, TemplateView):
    template_name = "control/crm/training.jinja"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        u, admin = self.request.user, _admin(self.request.user)
        qs = TrainingLog.objects.select_related("trainee", "trainer", "initiated_by")
        if not admin:
            qs = qs.filter(Q(trainee=u) | Q(trainer=u) | Q(initiated_by=u))
        logs = list(qs[:200])
        for t in logs:  # what this viewer may do with each row
            t.can_confirm = svc.can_confirm_training(t, u)
            t.can_mark = t.status == TrainingStatus.ASSIGNED and (admin or t.trainer_id == u.pk)
            t.can_assign = admin and t.status in (TrainingStatus.REQUESTED, TrainingStatus.ASSIGNED)
            t.can_cancel = t.status in (TrainingStatus.REQUESTED, TrainingStatus.ASSIGNED) and (
                admin or t.initiated_by_id == u.pk)
        ctx.update(logs=logs, is_admin=admin, people=svc.crm_people().exclude(pk=u.pk),
                   today=biz_today(),
                   waiting_for_me=sum(1 for t in logs if t.status == TrainingStatus.ASSIGNED
                                      and t.trainer_id == u.pk),
                   to_confirm=sum(1 for t in logs if t.can_confirm and not admin))
        return ctx


class TrainingCreateView(PlatformStaffRequiredMixin, View):
    """One endpoint, three modes: ``trained_by`` (I was trained by X),
    ``trained_student`` (I trained someone), ``request`` (wants training)."""

    def post(self, request):
        p, me = request.POST, request.user
        mode = p.get("mode", "trained_by")
        topic, when = p.get("topic", "").strip(), _date_or_today(p.get("trained_on"))
        try:
            if mode == "trained_student":
                student = _person(p.get("student"), exclude=me)
                svc.add_trained_student(trainer=me, student=student, name=p.get("student_name", ""),
                                        phone=p.get("student_phone", ""), topic=topic, trained_on=when)
                msg = ("Added — your student confirms it." if student else
                       "Added — a platform admin will verify it (they have no login yet).")
            elif mode == "request":
                for_user, fu = None, p.get("for_user", "")
                if fu.isdigit():  # admin may file one on someone's behalf
                    if not _admin(me):
                        raise svc.TrainingError("Only a platform admin can request for someone else.")
                    for_user = _person(fu)
                elif fu == "__new" and not p.get("student_name", "").strip():
                    raise svc.TrainingError("Type the new person's name.")
                svc.request_training(requester=me, for_user=for_user,
                                     name=p.get("student_name", "") if fu == "__new" else "",
                                     phone=p.get("student_phone", ""), topic=topic)
                msg = "Training request sent — the super admin will assign a trainer."
            elif mode == "trained_by":
                trainer = _person(p.get("trainer"), exclude=me)
                svc.log_trained_by(trainee=me, trainer=trainer, topic=topic, trained_on=when)
                msg = f"Sent to {svc.person_label(trainer)} to confirm."
            else:
                raise svc.TrainingError("Unknown entry type.")
            messages.success(request, msg)
        except svc.TrainingError as exc:
            messages.error(request, str(exc))
        return redirect("control:crm_training")


class TrainingRespondView(PlatformStaffRequiredMixin, View):
    def post(self, request, pk):
        log = get_object_or_404(TrainingLog, pk=pk)
        try:
            svc.respond_training(log, actor=request.user, ok=request.POST.get("action") != "reject")
        except svc.TrainingError as exc:
            raise PermissionDenied(str(exc))
        return redirect(_post_next(request, reverse("control:crm_training")))


class TrainingAssignView(PlatformAdminRequiredMixin, View):
    """Only the super admin assigns a trainer (no self-claiming)."""

    def post(self, request, pk):
        log = get_object_or_404(TrainingLog, pk=pk)
        trainer = _person(request.POST.get("trainer"))
        try:
            svc.assign_trainer(log, trainer, actor=request.user)
            messages.success(request, f"{svc.person_label(trainer)} will train {log.student_label}.")
        except svc.TrainingError as exc:
            messages.error(request, str(exc))
        return redirect(_post_next(request, reverse("control:crm_training")))


class TrainingMarkTrainedView(PlatformStaffRequiredMixin, View):
    def post(self, request, pk):
        log = get_object_or_404(TrainingLog, pk=pk)
        try:
            svc.mark_trained(log, actor=request.user)
        except svc.TrainingError as exc:
            raise PermissionDenied(str(exc))
        messages.success(request, "Marked trained — waiting for confirmation." if log.status ==
                         TrainingStatus.PENDING else "Marked trained and confirmed.")
        return redirect("control:crm_training")


class TrainingCancelView(PlatformStaffRequiredMixin, View):
    def post(self, request, pk):
        log = get_object_or_404(TrainingLog, pk=pk)
        try:
            svc.cancel_training_request(log, actor=request.user)
        except svc.TrainingError as exc:
            raise PermissionDenied(str(exc))
        return redirect("control:crm_training")


# ───────────────────────────── store work ─────────────────────────────

class WorkRequestForm(forms.Form):
    project = forms.ModelChoiceField(queryset=None)
    kind = forms.ChoiceField(choices=WorkKind.choices)
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}),
                           label="What needs doing?")

    def __init__(self, *a, projects, **kw):
        super().__init__(*a, **kw)
        self.fields["project"].queryset = projects


class WorkQueueView(PlatformStaffRequiredMixin, TemplateView):
    """Admin: all requests + assign. DGC: own requests + the work they hold."""

    template_name = "control/crm/work.jinja"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        u, admin = self.request.user, _admin(self.request.user)
        reqs = StoreWorkRequest.objects.select_related("project", "requested_by", "assigned_to")
        asg = StoreAssignment.objects.filter(is_active=True).select_related("project", "assignee")
        if not admin:
            reqs = reqs.filter(Q(requested_by=u) | Q(project__in=managed_projects(u)) | Q(assigned_to=u))
            asg = asg.filter(assignee=u)
        ctx.update(requests=reqs[:100], assignments=asg, is_admin=admin,
                   dgcs=svc.crm_people(), form=WorkRequestForm(projects=managed_projects(u)))
        return ctx


class WorkRequestCreateView(PlatformStaffRequiredMixin, View):
    def post(self, request):
        form = WorkRequestForm(request.POST, projects=managed_projects(request.user))
        if form.is_valid():
            svc.request_store_work(project=form.cleaned_data["project"], requested_by=request.user,
                                   kind=form.cleaned_data["kind"], note=form.cleaned_data["note"])
            messages.success(request, "Request sent to the super admin.")
        else:
            messages.error(request, "Choose one of your stores.")
        return redirect("control:crm_work")


class WorkAssignView(PlatformAdminRequiredMixin, View):
    """Only a platform admin assigns store work (no self-claiming)."""

    def post(self, request, pk):
        wr = get_object_or_404(StoreWorkRequest, pk=pk, status=WorkRequestStatus.OPEN)
        who = svc.dgc_users().filter(pk=request.POST.get("assignee") or 0).first() \
            or svc.crm_people().filter(pk=request.POST.get("assignee") or 0).first()
        if who is None:
            messages.error(request, "Choose who does this.")
        else:
            svc.assign_store_work(wr, who, actor=request.user)
            messages.success(request, f"Assigned to {svc.person_label(who)}.")
        return redirect(_post_next(request, reverse("control:crm_work")))


class WorkRevokeView(PlatformAdminRequiredMixin, View):
    def post(self, request, pk):
        svc.revoke_assignment(get_object_or_404(StoreAssignment, pk=pk, is_active=True), actor=request.user)
        messages.success(request, "Access removed.")
        return redirect("control:crm_work")


class WorkDoneView(PlatformStaffRequiredMixin, View):
    def post(self, request, pk):
        wr = get_object_or_404(StoreWorkRequest, pk=pk, status=WorkRequestStatus.ASSIGNED)
        if not (_admin(request.user) or wr.assigned_to_id == request.user.pk):
            raise PermissionDenied
        svc.complete_work_request(wr)
        svc.log_activity(actor=wr.assigned_to or request.user, kind=ActivityKind.STORE_SETUP
                         if wr.kind == WorkKind.CATALOG else ActivityKind.MAINTENANCE,
                         outcome=ActivityOutcome.DONE, project=wr.project, note="Work request completed")
        return redirect("control:crm_work")


class OwnerHelpView(StoreRoleRequiredMixin, ActiveProjectMixin, TemplateView):
    """Store owner / manager: ask the platform team for product-entry or
    maintenance help, and see / remove who currently has access."""

    template_name = "control/crm/owner_help.jinja"
    required_store_roles = OWNER_MANAGER

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        p = self.active_project
        ctx.update(requests=p.work_requests.all()[:20],
                   assignments=p.work_assignments.filter(is_active=True).select_related("assignee"),
                   kinds=WorkKind.choices)
        return ctx

    def post(self, request):
        p = self.active_project
        if request.POST.get("revoke", "").isdigit():
            a = get_object_or_404(StoreAssignment, pk=int(request.POST["revoke"]), project=p, is_active=True)
            svc.revoke_assignment(a, actor=request.user)
            messages.success(request, "Access removed.")
        else:
            kind = request.POST.get("kind")
            if kind in WorkKind.values:
                svc.request_store_work(project=p, requested_by=request.user, kind=kind,
                                       note=request.POST.get("note", ""))
                messages.success(request, "Request sent to the platform team.")
        return redirect("control:crm_owner_help")


class MyProfileView(PlatformStaffRequiredMixin, View):
    """A DGC's own lead preferences: pause new leads, cities covered."""

    def post(self, request):
        prof, _ = CrmProfile.objects.get_or_create(user=request.user)
        prof.accepts_leads = bool(request.POST.get("accepts_leads"))
        prof.cities = ", ".join(c.strip() for c in request.POST.get("cities", "").split(",") if c.strip())[:200]
        prof.save()
        messages.success(request, "Saved." if prof.accepts_leads else "Saved — you won't get new leads until you switch this back on.")
        return redirect(_post_next(request, reverse("control:crm_my_day")))


class MyServiceChargeView(PlatformStaffRequiredMixin, View):
    """A DGC sets (or clears) their own service charge per client — added on top of
    the commission in every 'what is a client worth to me' number."""

    MAX = Decimal("1000000")

    def post(self, request):
        raw = (request.POST.get("service_charge") or "").replace(",", "").replace("₹", "").strip() or "0"
        try:
            amount = Decimal(raw)
            if amount < 0 or amount > self.MAX or not amount.is_finite():
                raise ValueError
        except (ValueError, ArithmeticError):
            messages.error(request, "Enter your service charge as an amount in ₹ (0 to clear it).")
        else:
            prof, _ = CrmProfile.objects.get_or_create(user=request.user)
            prof.service_charge = amount.quantize(Decimal("1"))
            prof.save(update_fields=["service_charge", "updated_at"])
            messages.success(request, f"Your service charge is now {svc.inr(prof.service_charge)} per client." if amount
                             else "Your own service charge was cleared.")
        return redirect(_post_next(request, reverse("control:crm_my_day")) + "#possible")


class CrmSettingsForm(forms.ModelForm):
    STAGES = [("new", "New"), ("contacted", "Contacted"), ("interested", "Interested"),
              ("demo_booked", "Demo booked"), ("demo_done", "Demo done"), ("negotiating", "Negotiating")]

    class Meta:
        model = CrmSettings
        exclude = ["stage_probabilities", "created_at", "updated_at"]
        widgets = {"digest_emails": forms.Textarea(attrs={"rows": 1, "placeholder": "a@x.com, b@y.com"})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for key, label in self.STAGES:
            self.fields[f"p_{key}"] = forms.IntegerField(
                min_value=0, max_value=100, label=f"{label} %", initial=self.instance.probability(key))
        self.fields["quiet_after_hour"].widget.attrs.update(min=0, max=23)

    def _pct(self, name):
        v = self.cleaned_data[name]
        if not 1 <= v <= 100:
            raise forms.ValidationError("Use a percentage from 1 to 100.")
        return v

    def clean_value_commission_pct(self):
        return self._pct("value_commission_pct")

    def clean_conv_call_to_demo(self):
        return self._pct("conv_call_to_demo")

    def clean_conv_demo_to_trial(self):
        return self._pct("conv_demo_to_trial")

    def clean_conv_trial_to_paid(self):
        return self._pct("conv_trial_to_paid")

    def clean_working_days_month(self):
        v = self.cleaned_data["working_days_month"]
        if not 1 <= v <= 31:
            raise forms.ValidationError("Use 1 to 31 days.")
        return v

    def clean_min_ticket_override(self):
        v = self.cleaned_data["min_ticket_override"]
        if v is not None and v < 0:
            raise forms.ValidationError("Cannot be negative.")
        return v or None

    def clean_quiet_after_hour(self):
        h = self.cleaned_data["quiet_after_hour"]
        if not 0 <= h <= 23:
            raise forms.ValidationError("Use an hour from 0 to 23.")
        return h

    def clean_digest_emails(self):
        raw = self.cleaned_data["digest_emails"]
        emails = [e.strip() for e in raw.replace("\n", ",").split(",") if e.strip()]
        for e in emails:
            forms.EmailField().clean(e)
        return ", ".join(emails)

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.stage_probabilities = {k: self.cleaned_data[f"p_{k}"] for k, _ in self.STAGES}
        if commit:
            obj.save()
        return obj


class CrmSettingsView(PlatformAdminRequiredMixin, TemplateView):
    template_name = "control/crm/settings.jinja"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["form"] = kw.get("form") or CrmSettingsForm(instance=CrmSettings.load())
        ctx["capture_on"] = bool(getattr(settings, "CRM_CAPTURE_TOKEN", ""))
        ctx["capture_url"] = self.request.build_absolute_uri(reverse("crm_capture"))
        ctx["stage_fields"] = [f"p_{k}" for k, _ in CrmSettingsForm.STAGES]
        return ctx

    def post(self, request):
        form = CrmSettingsForm(request.POST, instance=CrmSettings.load())
        if form.is_valid():
            form.save()
            messages.success(request, "CRM settings saved.")
            return redirect("control:crm_settings")
        return self.render_to_response(self.get_context_data(form=form))


class CrmRunNowView(PlatformAdminRequiredMixin, View):
    """Run every automatic job once, right now, and say what each did."""

    def post(self, request):
        from apps.crm import automation

        res = automation.run_all()
        messages.success(
            request, f"Done — {res['trial_rescue']} trial task(s), {res['store_health']} store-health task(s), "
                     f"{res['recycled']} lead(s) returned to the pool"
                     f"{', quiet alert sent' if res['quiet_alert'] else ''}"
                     f"{', weekly digest sent' if res['weekly_digest'] else ''}.")
        return redirect("control:crm_settings")


class TemplateForm(forms.ModelForm):
    class Meta:
        model = MessageTemplate
        fields = ["kind", "stage", "title", "body", "order", "is_active"]
        widgets = {"body": forms.Textarea(attrs={"rows": 5}),
                   "title": forms.TextInput(attrs={"placeholder": "e.g. Intro"})}

    def clean(self):
        data = super().clean()
        if data.get("kind") == TemplateKind.WHATSAPP:
            data["stage"] = ""          # stage only applies to call scripts
        return data


class TemplateListView(PlatformAdminRequiredMixin, TemplateView):
    template_name = "control/crm/templates.jinja"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["whatsapp"] = MessageTemplate.objects.filter(kind=TemplateKind.WHATSAPP)
        ctx["scripts"] = MessageTemplate.objects.filter(kind=TemplateKind.SCRIPT)
        return ctx


class TemplateEditView(PlatformAdminRequiredMixin, TemplateView):
    template_name = "control/crm/template_form.jinja"

    def _obj(self, pk):
        return get_object_or_404(MessageTemplate, pk=pk) if pk else None

    def get_context_data(self, pk=None, **kw):
        ctx = super().get_context_data(**kw)
        obj = self._obj(pk)
        ctx.update(form=kw.get("form") or TemplateForm(instance=obj), obj=obj)
        return ctx

    def post(self, request, pk=None):
        obj = self._obj(pk)
        form = TemplateForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Template saved.")
            return redirect("control:crm_templates")
        return self.render_to_response(self.get_context_data(pk=pk, form=form))


class TemplateDeleteView(PlatformAdminRequiredMixin, View):
    def post(self, request, pk):
        get_object_or_404(MessageTemplate, pk=pk).delete()
        messages.success(request, "Template deleted.")
        return redirect("control:crm_templates")


# ───────────────────────────── insights + statements (admin) ─────────────────────────────

_INSIGHT_RANGES = [("7d", "7 days"), ("30d", "30 days"), ("month", "This month"), ("90d", "90 days")]


def _insight_range(key):
    today = biz_today()
    if key == "7d":
        return today - dt.timedelta(days=6), today, "Last 7 days"
    if key == "month":
        return today.replace(day=1), today, "This month"
    if key == "90d":
        return today - dt.timedelta(days=89), today, "Last 90 days"
    return today - dt.timedelta(days=29), today, "Last 30 days"


def _csv_cell(v):
    """Neutralise spreadsheet formula injection (a lead name like =HYPERLINK(...))."""
    t = "" if v is None else str(v)
    return "'" + t if t[:1] in ("=", "+", "-", "@", "\t", "\r") else t


def _csv_response(filename, header, rows):
    from django.http import HttpResponse

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([_csv_cell(c) for c in r])
    resp = HttpResponse("\ufeff" + buf.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


class InsightsView(PlatformStaffRequiredMixin, TemplateView):
    """Admins see the whole team (plus ad spend and logging checks); a DGC sees
    only their own leads — never other people's numbers."""

    template_name = "control/crm/insights.jinja"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        admin = _admin(self.request.user)
        me = None if admin else self.request.user
        key = self.request.GET.get("range", "30d")
        start, end, label = _insight_range(key)
        ctx.update(
            is_admin=admin, range_key=key, range_label=label, ranges=_INSIGHT_RANGES, start=start, end=end,
            sources=svc.source_roi(start, end, user=me), funnel=svc.funnel(user=me),
            stuck=svc.stuck_leads(user=me), forecast=svc.forecast(user=me),
            speed=svc.speed_to_lead(start, end, user=me),
            flags=svc.logging_flags(start, end) if admin else [],
            known_sources=(sorted(set(Lead.objects.exclude(source="").values_list("source", flat=True)
                                      ) | set(SourceSpend.objects.values_list("source", flat=True))) if admin else []),
            spends=SourceSpend.objects.all()[:8] if admin else [],
            this_month=biz_today().replace(day=1).isoformat()[:7])
        return ctx


class SpendView(PlatformAdminRequiredMixin, View):
    """Enter (or overwrite) the ad spend for a source in a month."""

    def post(self, request):
        source = request.POST.get("source", "").strip()[:60]
        raw_amount, raw_month = request.POST.get("amount", "").strip(), request.POST.get("month", "").strip()
        try:
            amount = Decimal(raw_amount)
            year, mon = [int(x) for x in raw_month.split("-")[:2]]
            month = dt.date(year, mon, 1)
            if not source or amount < 0 or amount > Decimal("99999999"):
                raise ValueError
        except (ValueError, ArithmeticError, TypeError):
            messages.error(request, "Enter a source, a month and a spend amount.")
        else:
            SourceSpend.objects.update_or_create(source=source, month=month, defaults={"amount": amount})
            messages.success(request, f"Saved ₹{amount} for {source}, {month:%b %Y}.")
        return redirect(_post_next(request, reverse("control:crm_insights")))


def _statement_month(request):
    raw = request.GET.get("month", "")
    try:
        y, m = [int(x) for x in raw.split("-")[:2]]
        return svc.month_bounds(y, m)
    except (ValueError, TypeError):
        today = biz_today()
        prev = today.replace(day=1) - dt.timedelta(days=1)          # default: last full month
        return svc.month_bounds(prev.year, prev.month)


class StatementsView(PlatformAdminRequiredMixin, TemplateView):
    template_name = "control/crm/statements.jinja"

    def get(self, request, *args, **kwargs):
        first, last = _statement_month(request)
        rows = svc.statement_rows(first, last)
        if request.GET.get("format") == "csv":
            return _csv_response(
                f"crm-statement-{first:%Y-%m}.csv",
                ["DGC", "Calls", "Spoke", "Demos", "Trained others", "Stores won", "Collected (verified ₹)",
                 "Commission pending ₹", "Commission approved ₹", "Commission paid ₹", "Commission total ₹"],
                [[r["name"], r["calls"], r["connected"], r["demos"], r["trained_others"], r["won"], r["collection"],
                  r["com_pending"], r["com_approved"], r["com_paid"], r["com_total"]] for r in rows])
        ctx = self.get_context_data(rows=rows, first=first, last=last, **kwargs)
        return self.render_to_response(ctx)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        first = kw["first"]
        prev = (first - dt.timedelta(days=1)).replace(day=1)
        nxt = (first + dt.timedelta(days=32)).replace(day=1)
        ctx.update(month_value=f"{first:%Y-%m}", prev_month=f"{prev:%Y-%m}", next_month=f"{nxt:%Y-%m}",
                   totals={k: sum(r[k] for r in kw["rows"]) for k in
                           ("calls", "demos", "won", "collection", "com_pending", "com_approved", "com_paid", "com_total")})
        return ctx


class StatementDetailView(PlatformAdminRequiredMixin, TemplateView):
    template_name = "control/crm/statement_detail.jinja"

    def get(self, request, pk, *args, **kwargs):
        from apps.billing.models import ManagerCommission

        person = get_object_or_404(svc.dgc_users(), pk=pk)
        first, last = _statement_month(request)
        lo, hi = svc.day_bounds(first, last)
        commissions = list(ManagerCommission.objects.filter(manager=person, created_at__gte=lo, created_at__lt=hi)
                           .select_related("subscription__project", "invoice").order_by("created_at"))
        collections = list(Collection.objects.filter(collected_by=person, collected_on__gte=first, collected_on__lte=last,
                                                     status=CollectionStatus.VERIFIED).select_related("project"))
        won = list(Lead.objects.filter(assigned_to=person, status=LeadStatus.WON, stage_changed_at__gte=lo,
                                       stage_changed_at__lt=hi))
        if request.GET.get("format") == "csv":
            rows = [["Commission", to_biz(c.created_at).date(), c.subscription.project.name, c.invoice.number, c.base_amount,
                     c.rate_pct, c.amount, c.status] for c in commissions]
            rows += [["Collection", c.collected_on, c.project.name if c.project else "", c.reference, "", "", c.amount,
                      c.get_mode_display()] for c in collections]
            rows += [["Store won", to_biz(l.stage_changed_at).date() if l.stage_changed_at else "", l.business or l.name, "", "", "", "", ""]
                     for l in won]
            return _csv_response(f"crm-statement-{person.pk}-{first:%Y-%m}.csv",
                                 ["Type", "Date", "Store", "Reference", "Base ₹", "Rate %", "Amount ₹", "Status / mode"], rows)
        row = svc.statement_rows(first, last)
        mine = next((r for r in row if r["user"].pk == person.pk), None)
        ctx = self.get_context_data(person=person, first=first, last=last, commissions=commissions,
                                    collections=collections, won=won, me=mine, month_value=f"{first:%Y-%m}", **kwargs)
        return self.render_to_response(ctx)


# ───────────────────────────── targets ─────────────────────────────

class TargetsView(PlatformAdminRequiredMixin, TemplateView):
    template_name = "control/crm/targets.jinja"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["defaults"] = svc.targets_for(None)
        ctx["metrics"] = TargetMetric.choices
        return ctx

    def post(self, request):
        for m, _ in TargetMetric.choices:
            v = request.POST.get(m, "")
            if v.isdigit():
                DailyTarget.objects.update_or_create(user=None, metric=m, defaults={"value": int(v)})
        messages.success(request, "Targets saved.")
        return redirect("control:crm_targets")
