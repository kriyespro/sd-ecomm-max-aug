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

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Q
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
from apps.core.services import record_audit
from apps.crm import services as svc
from apps.crm.models import (
    Activity,
    ActivityKind,
    ActivityOutcome,
    Collection,
    CollectionMode,
    CollectionStatus,
    DailyTarget,
    Lead,
    LeadStatus,
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
        ctx.update(range_key=key, range_label=label, ranges=_RANGES, rows=rows, active_only=active_only,
                   totals=totals, extras=svc.board_extras(), start=start, end=end)
        return ctx


class MyDayView(PlatformStaffRequiredMixin, TemplateView):
    template_name = "control/crm/my_day.jinja"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        u = self.request.user
        key = self.request.GET.get("range", "today")
        start, end, label = svc.resolve_range(key)
        rows, totals = svc.person_numbers(start, end, users=[u])
        today = timezone.localdate()
        skip = [int(i) for i in self.request.GET.get("skip", "").split(",")[:50] if i.isdigit()]
        queue = svc.call_queue(u, skip=skip)
        nxt = queue.first()
        ctx.update(
            nxt=nxt, queue_left=queue.count(), skip_ids=skip,
            nxt_activity=nxt.activities.select_related("actor")[:3] if nxt else [],
            skip_param=",".join(map(str, skip + ([nxt.pk] if nxt else []))),
            today_calls=rows[0]["calls"] if key == "today" else Activity.objects.filter(
                actor=u, kind=ActivityKind.CALL, occurred_at__gte=svc.day_bounds(today)[0]).count(),
            range_key=key, range_label=label, ranges=_RANGES, me=rows[0], targets=rows[0]["targets"],
            due_leads=Lead.objects.filter(assigned_to=u, next_follow_up__lte=today,
                                          status__in=[s for s in LeadStatus.values
                                                      if s not in ("won", "lost")])[:20],
            open_tasks=Task.objects.filter(assignee=u, status=TaskStatus.OPEN)[:20],
            recent=Activity.objects.filter(actor=u).select_related("lead")[:15],
            my_work=StoreAssignment.objects.filter(assignee=u, is_active=True).select_related("project"),
            pending_confirm=TrainingLog.objects.filter(trainer=u, status=TrainingStatus.PENDING)
            .select_related("trainee").count(),
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
                      or any(g.get(k) for k in ("q", "status", "assignee", "due", "page")))
        if not wants_list:
            return redirect("control:crm_lead_board")
        resp = super().get(request, *args, **kwargs)
        if g.get("view") == "list":
            resp.set_cookie(LEADS_VIEW_COOKIE, "list", max_age=60 * 60 * 24 * 365, samesite="Lax")
        return resp

    def get_queryset(self):
        g = self.request.GET
        qs = _lead_qs(self.request.user)
        if g.get("status") in LeadStatus.values:
            qs = qs.filter(status=g["status"])
        if g.get("assignee") == "none":
            qs = qs.filter(assigned_to__isnull=True)
        elif g.get("assignee", "").isdigit() and _admin(self.request.user):
            qs = qs.filter(assigned_to_id=int(g["assignee"]))
        if g.get("due"):
            qs = qs.filter(next_follow_up__lte=timezone.localdate())
        if g.get("q"):
            q = g["q"].strip()
            qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(business__icontains=q))
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        g = self.request.GET.copy()
        g.pop("page", None)
        ctx.update(statuses=LeadStatus.choices, is_admin=_admin(self.request.user),
                   # Inline status edit: everything except "won" (that has its own store flow)
                   editable_statuses=[c for c in LeadStatus.choices if c[0] != LeadStatus.WON],
                   people=svc.crm_people(), g=self.request.GET, form=LeadForm(),
                   today=timezone.localdate(), qs=(g.urlencode() + "&") if g else "")
        return ctx


class LeadCreateView(PlatformStaffRequiredMixin, View):
    def post(self, request):
        form = LeadForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Lead needs at least a name.")
            return redirect("control:crm_leads")
        lead = form.save(commit=False)
        lead.created_by = request.user
        if not _admin(request.user) or not request.POST.get("assigned_to"):
            lead.assigned_to = request.user
        else:
            lead.assigned_to = User.objects.filter(pk=request.POST["assigned_to"]).first()
        lead.assigned_by = request.user
        lead.save()
        messages.success(request, "Lead added.")
        return redirect(_post_next(request, reverse("control:crm_lead", kwargs={"pk": lead.pk})))


class LeadDetailView(PlatformStaffRequiredMixin, TemplateView):
    template_name = "control/crm/lead.jinja"

    def get_context_data(self, pk, **kw):
        ctx = super().get_context_data(**kw)
        lead = get_object_or_404(_lead_qs(self.request.user), pk=pk)
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


class LeadWonView(PlatformStaffRequiredMixin, View):
    """Mark won → hand to the existing store-create screen."""

    def post(self, request, pk):
        lead = get_object_or_404(_lead_qs(request.user), pk=pk)
        lead.status = LeadStatus.WON
        lead.save(update_fields=["status", "updated_at"])
        messages.success(request, "Marked won — create the store now.")
        return redirect("control:store_create")


class LeadImportSampleView(PlatformAdminRequiredMixin, View):
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


class LeadImportView(PlatformAdminRequiredMixin, View):
    """CSV: name,phone,business,city,source (header row optional)."""

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
        who = User.objects.filter(pk=request.POST.get("assignee") or 0).first()
        made = skipped = 0
        for i, row in enumerate(csv.reader(io.StringIO(text))):
            row = [c.strip() for c in row] + [""] * 5
            if i == 0 and row[0].lower() in ("name", "lead", "full name"):
                continue
            name, phone, biz, city, src = row[:5]
            if not name or (phone and Lead.objects.filter(phone=phone).exists()):
                skipped += 1
                continue
            if made >= 2000:
                break
            Lead.objects.create(name=name[:120], phone=phone[:20], business=biz[:160],
                                city=city[:80], source=(src or "import")[:60],
                                assigned_to=who, assigned_by=request.user if who else None,
                                created_by=request.user)
            made += 1
        messages.success(request, f"Imported {made} lead(s); skipped {skipped}.")
        return redirect("control:crm_leads")


# ───────────────────────────── quick log ─────────────────────────────

class LogActivityView(PlatformStaffRequiredMixin, View):
    """2-tap logger: kind + outcome (+ optional lead, note, follow-up date)."""

    def post(self, request):
        p = request.POST
        kind = p.get("kind")
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
            follow = timezone.localdate() + dt.timedelta(days=int(p["follow_in"]))
        elif p.get("follow_up"):
            try:
                follow = dt.date.fromisoformat(p["follow_up"])
            except ValueError:
                pass
        count = p.get("count", "1")
        svc.log_activity(actor=request.user, kind=kind, outcome=outcome, lead=lead,
                         project=project, note=p.get("note", ""),
                         count=int(count) if count.isdigit() else 1, follow_up=follow)
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
        return redirect("control:crm_tasks")


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

class TrainingForm(forms.ModelForm):
    class Meta:
        model = TrainingLog
        fields = ["trainer", "topic", "trained_on"]
        widgets = {"trained_on": forms.DateInput(attrs={"type": "date"}),
                   "topic": forms.TextInput(attrs={"placeholder": "e.g. Store setup (optional)"})}
        labels = {"trainer": "Trained by", "trained_on": "Date"}

    def __init__(self, *a, user=None, **kw):
        super().__init__(*a, **kw)
        qs = svc.crm_people()
        self.fields["trainer"].queryset = qs.exclude(pk=user.pk) if user else qs
        self.fields["trainer"].label_from_instance = svc.person_label
        self.fields["trainer"].empty_label = "Choose trainer…"
        self.fields["trained_on"].initial = timezone.localdate()


class TrainingListView(PlatformStaffRequiredMixin, TemplateView):
    template_name = "control/crm/training.jinja"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        u, admin = self.request.user, _admin(self.request.user)
        qs = TrainingLog.objects.select_related("trainee", "trainer")
        mine = qs if admin else qs.filter(Q(trainee=u) | Q(trainer=u))
        ctx.update(logs=mine[:200], is_admin=admin, form=TrainingForm(user=u))
        return ctx


class TrainingCreateView(PlatformStaffRequiredMixin, View):
    def post(self, request):
        form = TrainingForm(request.POST, user=request.user)
        if form.is_valid():
            t = form.save(commit=False)
            t.trainee = request.user
            t.save()
            messages.success(request, f"Sent to {svc.person_label(t.trainer)} to confirm.")
        else:
            messages.error(request, "Pick the DGC who trained you.")
        return redirect("control:crm_training")


class TrainingRespondView(PlatformStaffRequiredMixin, View):
    def post(self, request, pk):
        log = get_object_or_404(TrainingLog, pk=pk)
        if not (_admin(request.user) or log.trainer_id == request.user.pk):
            raise PermissionDenied("Only the trainer can confirm this.")
        svc.respond_training(log, actor=request.user, ok=request.POST.get("action") != "reject")
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
        return redirect("control:crm_work")


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
