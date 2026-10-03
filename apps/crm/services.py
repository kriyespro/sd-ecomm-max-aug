"""CRM actions + the numbers behind the "Today" board."""

import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.core.models import AuditLog
from apps.core.services import record_audit

from .models import (
    Activity,
    ActivityKind,
    ActivityOutcome,
    Collection,
    CollectionMode,
    CollectionStatus,
    DailyTarget,
    Lead,
    LeadStatus,
    OPEN_LEAD_STATUSES,
    StoreAssignment,
    StoreWorkRequest,
    TargetMetric,
    Task,
    TaskStatus,
    TrainingLog,
    TrainingStatus,
    WorkRequestStatus,
)

User = get_user_model()

DEFAULT_TARGETS = {TargetMetric.CALLS: 40, TargetMetric.DEMOS: 2, TargetMetric.COLLECTION: 0}


# ── who counts as a CRM person ─────────────────────────────────────────────

def crm_people():
    """Every platform-staff user: DGCs plus platform owners / superusers."""
    return User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | ~Q(profile__platform_role="none") & Q(profile__isnull=False)
    ).distinct().order_by("first_name", "email")


def dgc_users():
    return User.objects.filter(is_active=True, profile__platform_role="platform_manager"
                               ).order_by("first_name", "email")


def person_label(user):
    return (user.get_full_name() or user.email or user.get_username()) if user else "—"


# ── date helpers ───────────────────────────────────────────────────────────

def day_bounds(start, end=None):
    """Aware [start 00:00, end+1 00:00) datetimes for date range inclusive."""
    end = end or start
    tz = timezone.get_current_timezone()
    lo = timezone.make_aware(dt.datetime.combine(start, dt.time.min), tz)
    hi = timezone.make_aware(dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min), tz)
    return lo, hi


def resolve_range(key):
    today = timezone.localdate()
    if key == "yesterday":
        d = today - dt.timedelta(days=1)
        return d, d, "Yesterday"
    if key == "7d":
        return today - dt.timedelta(days=6), today, "Last 7 days"
    if key == "month":
        return today.replace(day=1), today, "This month"
    return today, today, "Today"


# ── activity / lead actions ────────────────────────────────────────────────

# Outcomes that should bring the lead back automatically (days) when the
# caller didn't pick a follow-up themselves.
_AUTO_FOLLOW_DAYS = {
    ActivityOutcome.NO_ANSWER: 1,
    ActivityOutcome.CALL_BACK: 1,
    ActivityOutcome.NOT_REACHABLE: 2,
}

# Outcome → what the lead moves to when quick-logged.
_CALL_STATUS = {
    ActivityOutcome.CONNECTED: LeadStatus.CONTACTED,
    ActivityOutcome.CALL_BACK: LeadStatus.CONTACTED,
    ActivityOutcome.NOT_INTERESTED: LeadStatus.LOST,
}


@transaction.atomic
def log_activity(*, actor, kind, outcome="", lead=None, project=None, note="", count=1,
                 follow_up=None):
    act = Activity.objects.create(
        actor=actor, kind=kind, outcome=outcome, lead=lead, project=project,
        note=note[:300], count=max(1, int(count or 1)),
    )
    if lead is not None:
        fields = []
        if kind == ActivityKind.DEMO and lead.status in (
                LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.INTERESTED, LeadStatus.DEMO_BOOKED):
            lead.status = LeadStatus.DEMO_DONE
            fields.append("status")
        elif kind in (ActivityKind.CALL, ActivityKind.WHATSAPP, ActivityKind.FOLLOW_UP) \
                and lead.status == LeadStatus.NEW and outcome in _CALL_STATUS:
            lead.status = _CALL_STATUS[outcome]
            fields.append("status")
        elif outcome == ActivityOutcome.NOT_INTERESTED and lead.is_open:
            lead.status = LeadStatus.LOST
            fields.append("status")
        today = timezone.localdate()
        if follow_up:
            lead.next_follow_up = follow_up
            fields.append("next_follow_up")
        elif outcome in _AUTO_FOLLOW_DAYS:
            lead.next_follow_up = today + dt.timedelta(days=_AUTO_FOLLOW_DAYS[outcome])
            fields.append("next_follow_up")
        elif lead.next_follow_up and lead.next_follow_up <= today:
            lead.next_follow_up = None  # this follow-up has now been worked
            fields.append("next_follow_up")
        if fields:
            lead.save(update_fields=fields + ["updated_at"])
    return act


def assign_leads(leads, assignee, *, actor):
    n = 0
    for lead in leads:
        lead.assigned_to, lead.assigned_by = assignee, actor
        lead.save(update_fields=["assigned_to", "assigned_by", "updated_at"])
        n += 1
    if n:
        record_audit(actor=actor, action=AuditLog.Action.UPDATE,
                     changes={"crm_assigned_leads": n, "to": getattr(assignee, "pk", None)})
    return n


def mark_task_done(task):
    task.status, task.done_at = TaskStatus.DONE, timezone.now()
    task.save(update_fields=["status", "done_at", "updated_at"])
    return task


# ── collections ────────────────────────────────────────────────────────────

def record_invoice_collection(invoice):
    """Idempotent: one verified Collection per paid platform invoice, credited
    to whoever earns commission on it (managing DGC, else referrer)."""
    if hasattr(invoice, "crm_collection"):
        return None
    sub = invoice.subscription
    credited = sub.manager_id or sub.referred_by_id
    if credited is None:
        return None
    return Collection.objects.create(
        collected_by_id=credited, amount=invoice.amount, mode=CollectionMode.SUBSCRIPTION,
        reference=getattr(invoice, "number", "") or "", project=sub.project, invoice=invoice,
        status=CollectionStatus.VERIFIED, verified_at=timezone.now(),
        collected_on=timezone.localdate(invoice.paid_at or timezone.now()),
    )


def verify_collection(collection, *, actor, ok=True):
    collection.status = CollectionStatus.VERIFIED if ok else CollectionStatus.REJECTED
    collection.verified_by, collection.verified_at = actor, timezone.now()
    collection.save(update_fields=["status", "verified_by", "verified_at", "updated_at"])
    return collection


# ── training ───────────────────────────────────────────────────────────────

def respond_training(log, *, actor, ok):
    """Trainer (or a platform admin) confirms / rejects a claimed training."""
    log.status = TrainingStatus.CONFIRMED if ok else TrainingStatus.REJECTED
    log.confirmed_at = timezone.now()
    log.save(update_fields=["status", "confirmed_at", "updated_at"])
    return log


# ── store work: request → admin assigns ────────────────────────────────────

def request_store_work(*, project, requested_by, kind, note=""):
    return StoreWorkRequest.objects.create(
        project=project, requested_by=requested_by, kind=kind, note=note[:1000])


@transaction.atomic
def assign_store_work(work_request, assignee, *, actor):
    """Platform admin hands a request to a DGC — grants scoped store access."""
    sa, _ = StoreAssignment.objects.get_or_create(
        project=work_request.project, assignee=assignee, scope=work_request.kind,
        is_active=True, defaults={"assigned_by": actor, "request": work_request})
    work_request.status = WorkRequestStatus.ASSIGNED
    work_request.assigned_to, work_request.assigned_by = assignee, actor
    work_request.assigned_at = timezone.now()
    work_request.save(update_fields=["status", "assigned_to", "assigned_by",
                                     "assigned_at", "updated_at"])
    record_audit(actor=actor, project=work_request.project, action=AuditLog.Action.UPDATE, target=sa,
                 changes={"store_work_assigned_to": assignee.pk, "scope": work_request.kind})
    return sa


def revoke_assignment(assignment, *, actor):
    assignment.is_active, assignment.revoked_at = False, timezone.now()
    assignment.save(update_fields=["is_active", "revoked_at", "updated_at"])
    record_audit(actor=actor, project=assignment.project, action=AuditLog.Action.UPDATE, target=assignment,
                 changes={"store_work_revoked": assignment.assignee_id})
    return assignment


def complete_work_request(work_request):
    work_request.status, work_request.done_at = WorkRequestStatus.DONE, timezone.now()
    work_request.save(update_fields=["status", "done_at", "updated_at"])
    StoreAssignment.objects.filter(request=work_request, is_active=True).update(
        is_active=False, revoked_at=timezone.now())


# ── the caller's queue ─────────────────────────────────────────────────────

def call_queue(user, *, skip=()):
    """Open leads for ``user`` in the order they should be worked: due
    follow-ups first (oldest first), then fresh leads. A lead already worked
    today with no follow-up date drops out until tomorrow."""
    from django.db.models import Exists, F, OuterRef

    today = timezone.localdate()
    lo, _ = day_bounds(today)
    worked = Activity.objects.filter(actor=user, lead=OuterRef("pk"), occurred_at__gte=lo)
    qs = (Lead.objects.filter(assigned_to=user, status__in=OPEN_LEAD_STATUSES)
          .annotate(worked_today=Exists(worked))
          .filter(Q(next_follow_up__lte=today) | Q(next_follow_up__isnull=True, worked_today=False))
          .order_by(F("next_follow_up").asc(nulls_last=True), "created_at"))
    if skip:
        qs = qs.exclude(pk__in=list(skip))
    return qs


# ── targets ────────────────────────────────────────────────────────────────

def targets_for(user):
    out = dict(DEFAULT_TARGETS)
    rows = DailyTarget.objects.filter(Q(user__isnull=True) | Q(user=user)).order_by("user_id")
    for r in rows:  # nulls first, user-specific override last
        out[r.metric] = r.value
    return out


# ── reporting ──────────────────────────────────────────────────────────────

def person_numbers(start, end=None, *, users=None):
    """Per-person counts for [start, end]. Returns (rows, totals)."""
    lo, hi = day_bounds(start, end)
    end = end or start
    acts = Activity.objects.filter(occurred_at__gte=lo, occurred_at__lt=hi)
    if users is not None:
        acts = acts.filter(actor__in=users)
    a = {}
    for row in acts.values("actor", "kind").annotate(n=Count("id"), units=Sum("count")):
        d = a.setdefault(row["actor"], {})
        d[row["kind"]] = row
    conn = {r["actor"]: r["n"] for r in acts.filter(
        kind=ActivityKind.CALL, outcome=ActivityOutcome.CONNECTED).values("actor").annotate(n=Count("id"))}

    cols = Collection.objects.filter(collected_on__gte=start, collected_on__lte=end)
    if users is not None:
        cols = cols.filter(collected_by__in=users)
    money = {}
    for r in cols.values("collected_by", "status").annotate(t=Sum("amount")):
        money.setdefault(r["collected_by"], {})[r["status"]] = r["t"] or Decimal("0")

    tr = TrainingLog.objects.filter(trained_on__gte=start, trained_on__lte=end,
                                    status=TrainingStatus.CONFIRMED)
    trained = {r["trainee"]: r["n"] for r in tr.values("trainee").annotate(n=Count("id"))}
    trained_others = {r["trainer"]: r["n"] for r in tr.values("trainer").annotate(n=Count("id"))}

    people = users if users is not None else crm_people()
    rows = []
    for u in people:
        d = a.get(u.pk, {})
        g = lambda k: (d.get(k) or {}).get("n", 0)  # noqa: E731
        m = money.get(u.pk, {})
        r = {
            "user": u, "name": person_label(u),
            "calls": g(ActivityKind.CALL), "connected": conn.get(u.pk, 0),
            "demos": g(ActivityKind.DEMO),
            "whatsapp": g(ActivityKind.WHATSAPP),
            "products": (d.get(ActivityKind.PRODUCT_ENTRY) or {}).get("units") or 0,
            "store_work": g(ActivityKind.STORE_SETUP) + g(ActivityKind.MAINTENANCE),
            "trained": trained.get(u.pk, 0),
            "trained_others": trained_others.get(u.pk, 0),
            "collection": m.get(CollectionStatus.VERIFIED, Decimal("0")),
            "collection_pending": m.get(CollectionStatus.UNVERIFIED, Decimal("0")),
        }
        r["targets"] = targets_for(u)
        rows.append(r)
    totals = {k: sum(r[k] for r in rows) for k in (
        "calls", "connected", "demos", "whatsapp", "products", "store_work",
        "trained", "collection", "collection_pending")}
    totals["connect_pct"] = round(100 * totals["connected"] / totals["calls"]) if totals["calls"] else 0
    return rows, totals


def board_extras():
    today = timezone.localdate()
    return {
        "won_today": Lead.objects.filter(status=LeadStatus.WON, updated_at__date=today).count(),
        "overdue_follow_ups": Lead.objects.filter(
            next_follow_up__lt=today, status__in=[s for s in LeadStatus.values
                                                  if s not in (LeadStatus.WON, LeadStatus.LOST)]).count(),
        "unverified": Collection.objects.filter(status=CollectionStatus.UNVERIFIED).count(),
        "pending_training": TrainingLog.objects.filter(status=TrainingStatus.PENDING).count(),
        "open_requests": StoreWorkRequest.objects.filter(status=WorkRequestStatus.OPEN).count(),
    }


# ── kanban board ───────────────────────────────────────────────────────────

BOARD_STAGES = [LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.INTERESTED,
                LeadStatus.DEMO_BOOKED, LeadStatus.DEMO_DONE, LeadStatus.NEGOTIATING]
BOARD_LIMIT = 30
BOARD_LIMIT_FULL = 300
STALE_DAYS = 7


class MoveError(Exception):
    """Raised for a stage move the board must refuse (message is user-facing)."""


def move_lead(lead, new_status, *, actor, reason=""):
    """Move a lead to a stage and write it into the lead's history.

    "Won" is deliberately not reachable here: it has its own flow that creates
    the store, so a drag can never mark a deal won without one.
    """
    if new_status not in LeadStatus.values:
        raise MoveError("Unknown stage.")
    if new_status == LeadStatus.WON:
        raise MoveError("Use “Deal won” to create the store.")
    if lead.status == LeadStatus.WON:
        raise MoveError("This deal is already won.")
    if new_status == lead.status:
        return lead
    old = lead.get_status_display()
    lead.status = new_status
    lead.save(update_fields=["status", "updated_at"])
    note = f"{old} → {lead.get_status_display()}"
    if reason:
        note += f" ({reason[:80]})"
    Activity.objects.create(actor=actor, kind=ActivityKind.STAGE, lead=lead, note=note[:300])
    return lead


def board_columns(user, *, owner=None, q="", full=None, admin=False):
    """Everything the kanban needs: per-stage cards (capped), true counts,
    stale counts, plus Won / Lost totals.

    ``owner``: None = the caller's own leads (DGC) / everyone (admin);
    "none" = unassigned; a user id = that person (admin only).
    """
    from django.db.models import Case, Exists, F, IntegerField, OuterRef, Subquery, When

    today = timezone.localdate()
    now = timezone.now()
    cutoff = now - dt.timedelta(days=STALE_DAYS)

    base = Lead.objects.all()
    if not admin:
        base = base.filter(assigned_to=user)
    elif owner == "none":
        base = base.filter(assigned_to__isnull=True)
    elif owner and str(owner).isdigit():
        base = base.filter(assigned_to_id=int(owner))
    if q:
        base = base.filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(business__icontains=q))

    counts = {r["status"]: r["n"] for r in base.values("status").annotate(n=Count("id"))}

    recent = Activity.objects.filter(lead=OuterRef("pk"), occurred_at__gte=cutoff)
    last = Activity.objects.filter(lead=OuterRef("pk")).order_by("-occurred_at").values("occurred_at")[:1]
    annotated = base.annotate(has_recent=Exists(recent), last_touch=Subquery(last))
    open_q = annotated.filter(status__in=BOARD_STAGES)
    stale_q = Q(has_recent=False, created_at__lt=cutoff)
    stale_counts = {r["status"]: r["n"] for r in
                    open_q.filter(stale_q).values("status").annotate(n=Count("id"))}

    columns = []
    for st in BOARD_STAGES:
        cap = BOARD_LIMIT_FULL if full == st else BOARD_LIMIT
        qs = (open_q.filter(status=st).select_related("assigned_to")
              .annotate(overdue=Case(When(next_follow_up__lte=today, then=0), default=1,
                                     output_field=IntegerField()))
              .order_by("overdue", F("last_touch").asc(nulls_first=True), "-created_at"))
        cards = list(qs[:cap])
        for c in cards:
            ref = c.last_touch or c.created_at
            c.stale = (not c.has_recent) and c.created_at < cutoff
            c.idle_days = (now - ref).days
            c.stage_days = (now - (c.stage_changed_at or c.created_at)).days
            c.overdue_flag = bool(c.next_follow_up and c.next_follow_up <= today)
        columns.append({
            "status": st.value, "label": st.label, "cards": cards,
            "count": counts.get(st.value, 0), "stale": stale_counts.get(st.value, 0),
            "more": max(0, counts.get(st.value, 0) - len(cards)),
        })
    return {"columns": columns, "won": counts.get(LeadStatus.WON.value, 0),
            "lost": counts.get(LeadStatus.LOST.value, 0), "today": today}
