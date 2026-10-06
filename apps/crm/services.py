"""CRM actions + the numbers behind the "Today" board."""

import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from apps.core.models import AuditLog
from apps.core.services import record_audit

from . import tz as _tz
from .tz import biz_today
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
    CrmProfile,
    CrmSettings,
    normalize_phone,
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

FRESH_HOURS = 24   # an untouched inbound lead stays 'fresh' (top of the queue) this long

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

day_bounds = _tz.day_bounds   # business-time day boundaries (see apps/crm/tz.py)


def resolve_range(key):
    today = biz_today()
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
                 follow_up=None, occurred_at=None):
    act = Activity.objects.create(
        actor=actor, kind=kind, outcome=outcome, lead=lead, project=project,
        note=note[:300], count=max(1, int(count or 1)),
        occurred_at=occurred_at or timezone.now(),
    )
    if lead is not None:
        fields = []
        if lead.first_touch_at is None and kind in (
                ActivityKind.CALL, ActivityKind.WHATSAPP, ActivityKind.DEMO, ActivityKind.FOLLOW_UP):
            lead.first_touch_at = occurred_at or timezone.now()
            fields.append("first_touch_at")
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
        today = biz_today()
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
        collected_on=biz_today(invoice.paid_at or timezone.now()),
    )


def verify_collection(collection, *, actor, ok=True):
    collection.status = CollectionStatus.VERIFIED if ok else CollectionStatus.REJECTED
    collection.verified_by, collection.verified_at = actor, timezone.now()
    collection.save(update_fields=["status", "verified_by", "verified_at", "updated_at"])
    return collection


# ── training ───────────────────────────────────────────────────────────────

class TrainingError(Exception):
    """A training action the caller isn't allowed to / can't do (user-facing text)."""


def can_confirm_training(log, user):
    """Platform admins can confirm anything pending; otherwise only the *other*
    side of the log (never whoever created it, never a name-only student)."""
    if log.status != TrainingStatus.PENDING:
        return False
    if getattr(getattr(user, "profile", None), "is_platform_admin", False) or user.is_superuser:
        return True
    return user.pk in (log.trainee_id, log.trainer_id) and user.pk != log.initiated_by_id


def respond_training(log, *, actor, ok):
    """Confirm / reject a pending training (see ``can_confirm_training``)."""
    if not can_confirm_training(log, actor):
        raise TrainingError("Only the other person in this training (or a platform admin) can confirm it.")
    log.status = TrainingStatus.CONFIRMED if ok else TrainingStatus.REJECTED
    log.confirmed_at = timezone.now()
    log.save(update_fields=["status", "confirmed_at", "updated_at"])
    return log


def _clean_student(name, phone):
    return (name or "").strip()[:120], (phone or "").strip()[:20]


def log_trained_by(*, trainee, trainer, topic="", trained_on=None):
    """"I was trained by X" — the trainer confirms."""
    if trainer is None or trainer.pk == trainee.pk:
        raise TrainingError("Pick the DGC who trained you.")
    return TrainingLog.objects.create(
        trainee=trainee, trainer=trainer, topic=topic[:160], initiated_by=trainee,
        trained_on=trained_on or biz_today(), status=TrainingStatus.PENDING)


def add_trained_student(*, trainer, student=None, name="", phone="", topic="", trained_on=None):
    """"I trained this person" — an existing account confirms it; a name-only
    student (no login yet) can only be confirmed by a platform admin."""
    name, phone = _clean_student(name, phone)
    if student is not None and student.pk == trainer.pk:
        raise TrainingError("You can't be your own student.")
    if student is None and not name:
        raise TrainingError("Choose a student or type their name.")
    return TrainingLog.objects.create(
        trainer=trainer, trainee=student, student_name="" if student else name,
        student_phone="" if student else phone, topic=topic[:160], initiated_by=trainer,
        trained_on=trained_on or biz_today(), status=TrainingStatus.PENDING)


def request_training(*, requester, for_user=None, name="", phone="", topic=""):
    """"Wants training" — no trainer yet; a platform admin assigns one.
    ``for_user`` None + no name = the requester asks for themselves."""
    name, phone = _clean_student(name, phone)
    if for_user is None and not name:
        for_user = requester
    return TrainingLog.objects.create(
        trainee=for_user, student_name="" if for_user else name,
        student_phone="" if for_user else phone, topic=topic[:160],
        initiated_by=requester, status=TrainingStatus.REQUESTED)


def assign_trainer(log, trainer, *, actor):
    """Platform admin picks the trainer for a request."""
    if log.status not in (TrainingStatus.REQUESTED, TrainingStatus.ASSIGNED):
        raise TrainingError("This training is already done.")
    if trainer is None:
        raise TrainingError("Choose a trainer.")
    if log.trainee_id and trainer.pk == log.trainee_id:
        raise TrainingError("A person can't train themselves.")
    log.trainer, log.assigned_by, log.assigned_at = trainer, actor, timezone.now()
    log.status = TrainingStatus.ASSIGNED
    log.save(update_fields=["trainer", "assigned_by", "assigned_at", "status", "updated_at"])
    record_audit(actor=actor, action=AuditLog.Action.UPDATE, target=log,
                 changes={"crm_training_trainer": trainer.pk})
    return log


def mark_trained(log, *, actor):
    """The assigned trainer says it's done -> the student confirms. A platform
    admin marking it done closes it straight to confirmed."""
    if log.status != TrainingStatus.ASSIGNED:
        raise TrainingError("Nothing to mark — this isn't an assigned training.")
    is_admin = actor.is_superuser or getattr(getattr(actor, "profile", None), "is_platform_admin", False)
    if not (is_admin or log.trainer_id == actor.pk):
        raise TrainingError("Only the assigned trainer can mark this trained.")
    log.trained_on = biz_today()
    log.initiated_by = actor
    if is_admin:
        log.status, log.confirmed_at = TrainingStatus.CONFIRMED, timezone.now()
        log.save(update_fields=["trained_on", "initiated_by", "status", "confirmed_at", "updated_at"])
    else:
        log.status = TrainingStatus.PENDING
        log.save(update_fields=["trained_on", "initiated_by", "status", "updated_at"])
    return log


def cancel_training_request(log, *, actor):
    """Requester or platform admin withdraws a request that isn't done yet."""
    is_admin = actor.is_superuser or getattr(getattr(actor, "profile", None), "is_platform_admin", False)
    if log.status not in (TrainingStatus.REQUESTED, TrainingStatus.ASSIGNED):
        raise TrainingError("Only open requests can be cancelled.")
    if not (is_admin or log.initiated_by_id == actor.pk):
        raise TrainingError("Only the requester or a platform admin can cancel this.")
    log.status = TrainingStatus.REJECTED
    log.save(update_fields=["status", "updated_at"])
    return log


def trainings_to_confirm(user):
    """Pending trainings where ``user`` is the one who has to confirm."""
    return TrainingLog.objects.filter(status=TrainingStatus.PENDING).filter(
        Q(trainee=user) | Q(trainer=user)).exclude(initiated_by=user)


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


# ── archive ────────────────────────────────────────────────────────────────

BULK_ARCHIVE_MAX = 500


def archive_leads(leads_qs, *, actor, restore=False):
    """Archive (or restore) the leads in ``leads_qs``; returns how many changed.
    Callers pass an already access-scoped queryset."""
    if restore:
        n = leads_qs.filter(is_archived=True).update(
            is_archived=False, archived_at=None, archived_by=None, updated_at=timezone.now())
    else:
        n = leads_qs.filter(is_archived=False).update(
            is_archived=True, archived_at=timezone.now(), archived_by=actor, updated_at=timezone.now())
    if n:
        record_audit(actor=actor, action=AuditLog.Action.UPDATE,
                     changes={"crm_leads_restored" if restore else "crm_leads_archived": n})
    return n


# ── the caller's queue ─────────────────────────────────────────────────────

def call_queue(user, *, skip=()):
    """Open leads for ``user`` in the order they should be worked: due
    follow-ups first (oldest first), then fresh leads. A lead already worked
    today with no follow-up date drops out until tomorrow."""
    from django.db.models import Exists, F, OuterRef

    today = biz_today()
    lo, _ = day_bounds(today)
    worked = Activity.objects.filter(actor=user, lead=OuterRef("pk"), occurred_at__gte=lo)
    from django.db.models import Case, IntegerField, Value, When

    fresh_cut = timezone.now() - dt.timedelta(hours=FRESH_HOURS)
    # Rank: 0 = brand-new inbound lead nobody has touched, 1 = due follow-up,
    # then hotter stages before colder ones (a demo-done lead beats a cold one).
    hot = Case(
        When(status=LeadStatus.NEGOTIATING, then=Value(0)), When(status=LeadStatus.DEMO_DONE, then=Value(1)),
        When(status=LeadStatus.DEMO_BOOKED, then=Value(2)), When(status=LeadStatus.INTERESTED, then=Value(3)),
        When(status=LeadStatus.CONTACTED, then=Value(4)), default=Value(5), output_field=IntegerField())
    qs = (Lead.objects.filter(assigned_to=user, status__in=OPEN_LEAD_STATUSES, is_archived=False)
          .annotate(worked_today=Exists(worked),
                    fresh_rank=Case(When(first_touch_at__isnull=True, assigned_at__gte=fresh_cut, then=Value(0)),
                                    default=Value(1), output_field=IntegerField()),
                    heat=hot)
          .filter(Q(next_follow_up__lte=today) | Q(next_follow_up__isnull=True, worked_today=False)
                  | Q(fresh_rank=0))
          .order_by("fresh_rank", F("next_follow_up").asc(nulls_last=True), "heat", "created_at"))
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
    last_seen = {r["actor"]: r["m"] for r in Activity.objects.filter(actor__in=people)
                 .values("actor").annotate(m=Max("occurred_at"))}
    rows = []
    for u in people:
        d = a.get(u.pk, {})
        g = lambda k: (d.get(k) or {}).get("n", 0)  # noqa: E731
        m = money.get(u.pk, {})
        r = {
            "user": u, "name": person_label(u),
            "last_active": last_seen.get(u.pk), "last_active_label": time_ago(last_seen.get(u.pk)),
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
    if users is None:  # board-wide: also count students who have no login yet
        totals["trained"] = tr.count()
    totals["connect_pct"] = round(100 * totals["connected"] / totals["calls"]) if totals["calls"] else 0
    return rows, totals


def time_ago(when):
    """'just now' / '12m ago' / '3h ago' / 'yesterday' / '5d ago' / 'never'."""
    if when is None:
        return "never"
    secs = int((timezone.now() - when).total_seconds())
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    days = secs // 86400
    return "yesterday" if days == 1 else f"{days}d ago"


def board_extras():
    today = biz_today()
    return {
        "won_today": Lead.objects.filter(status=LeadStatus.WON, stage_changed_at__gte=day_bounds(today)[0],
                                         stage_changed_at__lt=day_bounds(today)[1]).count(),
        "overdue_follow_ups": Lead.objects.filter(
            is_archived=False, next_follow_up__lt=today, status__in=[s for s in LeadStatus.values
                                                  if s not in (LeadStatus.WON, LeadStatus.LOST)]).count(),
        "unverified": Collection.objects.filter(status=CollectionStatus.UNVERIFIED).count(),
        "pending_training": TrainingLog.objects.filter(status=TrainingStatus.PENDING).count(),
        "training_requests": TrainingLog.objects.filter(status=TrainingStatus.REQUESTED).count(),
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

    today = biz_today()
    now = timezone.now()
    cutoff = now - dt.timedelta(days=STALE_DAYS)

    base = Lead.objects.filter(is_archived=False)
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
            c.demo_local = c.demo_at.astimezone(_biz_tz()) if c.demo_at else None
        columns.append({
            "status": st.value, "label": st.label, "cards": cards,
            "count": counts.get(st.value, 0), "stale": stale_counts.get(st.value, 0),
            "more": max(0, counts.get(st.value, 0) - len(cards)),
        })
    return {"columns": columns, "won": counts.get(LeadStatus.WON.value, 0),
            "lost": counts.get(LeadStatus.LOST.value, 0), "today": today}


# ── super-admin power tools ────────────────────────────────────────────────

def pipeline_counts():
    """Team-wide open pipeline for the board's one-row funnel."""
    qs = Lead.objects.filter(is_archived=False)
    counts = {r["status"]: r["n"] for r in qs.values("status").annotate(n=Count("id"))}
    return {
        "stages": [(st.value, st.label, counts.get(st.value, 0)) for st in BOARD_STAGES],
        "won": counts.get(LeadStatus.WON.value, 0),
        "lost": counts.get(LeadStatus.LOST.value, 0),
        "unassigned": qs.filter(assigned_to__isnull=True, status__in=OPEN_LEAD_STATUSES).count(),
    }


def distribute_leads(leads, assignees, *, actor):
    """Spread ``leads`` over ``assignees`` so everyone ends up with a similar
    number of open leads: each lead goes to whoever currently holds the fewest
    (ties by id). Returns {user_id: how many they received}."""
    assignees = list(assignees)
    leads = list(leads)
    if not assignees or not leads:
        return {}
    load = {u.pk: 0 for u in assignees}
    # The leads being handed out don't count as anyone's existing workload —
    # otherwise re-spreading leads someone already holds would starve them.
    held = (Lead.objects.filter(assigned_to__in=assignees, is_archived=False,
                                status__in=OPEN_LEAD_STATUSES)
            .exclude(pk__in=[l.pk for l in leads])
            .values("assigned_to").annotate(n=Count("id")))
    for r in held:
        load[r["assigned_to"]] = r["n"]
    got = {u.pk: 0 for u in assignees}
    now = timezone.now()
    for lead in leads:
        target = min(assignees, key=lambda u: (load[u.pk], u.pk))
        lead.assigned_to_id, lead.assigned_by_id, lead.updated_at = target.pk, actor.pk, now
        load[target.pk] += 1
        got[target.pk] += 1
    Lead.objects.bulk_update(leads, ["assigned_to", "assigned_by", "updated_at"])
    record_audit(actor=actor, action=AuditLog.Action.UPDATE,
                 changes={"crm_leads_distributed": len(leads), "to": sorted(got)})
    return got


def transfer_leads(from_user, to_users, *, actor):
    """Hand every open, un-archived lead of ``from_user`` to ``to_users`` (spread
    evenly). For when a DGC leaves or goes quiet."""
    to_users = [u for u in to_users if u.pk != from_user.pk]
    leads = Lead.objects.filter(assigned_to=from_user, is_archived=False, status__in=OPEN_LEAD_STATUSES)
    return distribute_leads(leads, to_users, actor=actor)


def admin_inbox(limit=5):
    """Everything waiting on a platform admin, with enough detail to act on it
    from the board. Each section: {"rows": [...first ``limit``], "total": n}."""
    def section(qs):
        total = qs.count()
        return {"rows": list(qs[:limit]), "total": total, "more": max(0, total - limit)}

    return {
        "collections": section(Collection.objects.filter(status=CollectionStatus.UNVERIFIED)
                               .select_related("collected_by", "project").order_by("created_at")),
        "work": section(StoreWorkRequest.objects.filter(status=WorkRequestStatus.OPEN)
                        .select_related("project", "requested_by").order_by("created_at")),
        "training_requests": section(TrainingLog.objects.filter(status=TrainingStatus.REQUESTED)
                                     .select_related("trainee", "initiated_by").order_by("created_at")),
        "trainings": section(TrainingLog.objects.filter(status=TrainingStatus.PENDING)
                             .select_related("trainee", "trainer", "initiated_by").order_by("created_at")),
    }


# ── lead intake + speed-to-lead routing ────────────────────────────────────

def phone_exists(phone, *, exclude_pk=None):
    """Is this person already in the CRM? Matches on the normalised number, so
    "+91 98765 43210" finds "9876543210"."""
    norm = normalize_phone(phone)
    if len(norm) < 7:           # too short to match reliably
        return False
    qs = Lead.objects.filter(phone_norm=norm)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


def _open_loads(user_ids):
    rows = (Lead.objects.filter(assigned_to__in=user_ids, is_archived=False, status__in=OPEN_LEAD_STATUSES)
            .values("assigned_to").annotate(n=Count("id")))
    load = {pk: 0 for pk in user_ids}
    for r in rows:
        load[r["assigned_to"]] = r["n"]
    return load


def eligible_dgcs():
    """Active DGCs who haven't switched themselves off for new leads."""
    off = set(CrmProfile.objects.filter(accepts_leads=False).values_list("user_id", flat=True))
    return [u for u in dgc_users().select_related("crm_profile") if u.pk not in off]


def pick_dgc(lead, cfg=None):
    """Who should get this lead? A DGC covering the lead's city wins; otherwise
    the least-loaded available DGC. Respects the per-DGC capacity cap."""
    cfg = cfg or CrmSettings.load()
    cands = eligible_dgcs()
    if not cands:
        return None
    load = _open_loads([u.pk for u in cands])
    if cfg.capacity_per_dgc:
        cands = [u for u in cands if load[u.pk] < cfg.capacity_per_dgc]
        if not cands:
            return None
    city = (lead.city or "").strip().lower()
    local = []
    if city:
        for u in cands:
            prof = getattr(u, "crm_profile", None)
            if prof and any(kw in city or city in kw for kw in prof.city_list()):
                local.append(u)
    pool = local or cands
    return min(pool, key=lambda u: (load[u.pk], u.pk))


def alert_new_lead(lead):
    """Tell the DGC a lead was just handed to them (best-effort email; the
    in-app banner on My day needs no sending)."""
    cfg = CrmSettings.load()
    who = lead.assigned_to
    if not (cfg.alert_email and who and who.email):
        return False
    try:
        from django.core.mail import send_mail
        from django.urls import reverse

        path = reverse("control:crm_lead", kwargs={"pk": lead.pk})
        body = (f"New lead: {lead.name}\n{lead.business or ''} {lead.city or ''}\nPhone: {lead.phone or '—'}\n"
                f"Source: {lead.source or '—'}\n\nCall within 5 minutes — it makes the difference.\n"
                f"Open it: {path}\n")
        send_mail(f"📞 New lead: {lead.name} — call now", body, None, [who.email], fail_silently=True)
        return True
    except Exception:  # noqa: BLE001 — an email hiccup must never block lead intake
        import logging

        logging.getLogger(__name__).exception("crm: lead alert email failed for lead %s", lead.pk)
        return False


def route_lead(lead, *, actor=None):
    """Auto-assign an unassigned lead. Returns the DGC or None (left in the pool)."""
    cfg = CrmSettings.load()
    if lead.assigned_to_id or not cfg.auto_assign:
        return None
    target = pick_dgc(lead, cfg)
    if target is None:
        return None
    lead.assigned_to, lead.assigned_by, lead.auto_assigned = target, actor, True
    lead.assigned_at = timezone.now()
    lead.save(update_fields=["assigned_to", "assigned_by", "auto_assigned", "assigned_at", "updated_at"])
    alert_new_lead(lead)
    return target


def ingest_lead(*, name="", phone="", business="", city="", source="", notes="", acquisition=None,
                assign_to=None, converted_project=None, route=True):
    """Single door for inbound leads (signup hook, capture webhook, forms).

    * duplicate phone -> returns (existing_lead, False), nothing is changed;
    * ``assign_to`` (e.g. the referring DGC) beats auto-routing;
    * otherwise the lead is auto-routed (city, then least-loaded).
    Returns ``(lead, created)``."""
    name = (name or "").strip()[:120]
    phone = (phone or "").strip()[:20]
    if not name and not phone:
        raise ValueError("A name or a phone number is required.")
    if phone and phone_exists(phone):
        return Lead.objects.filter(phone_norm=normalize_phone(phone)).first(), False
    lead = Lead.objects.create(
        name=name or phone, phone=phone, business=(business or "")[:160], city=(city or "")[:80],
        source=(source or "inbound")[:60], notes=(notes or "")[:2000], acquisition=acquisition or {},
        converted_project=converted_project, assigned_to=assign_to,
        auto_assigned=False)
    if assign_to is not None:
        alert_new_lead(lead)
    elif route:
        route_lead(lead)
    return lead, True


def lead_from_signup(project, *, name, phone, city="", ref_user=None):
    """A store owner just self-signed-up (often from an ad): make them a lead and
    get a DGC on the phone fast. The referring DGC, if any, keeps the lead."""
    src = project.signup_source or {}
    label = src.get("utm_source") or src.get("lp") or "organic"
    return ingest_lead(
        name=name, phone=phone, business=project.name, city=city,
        source=f"Signup · {label}"[:60], notes=f"Self-signup, trial started. Store: {project.name}",
        acquisition=dict(src), assign_to=ref_user, converted_project=project)


# ── DGC tools: templates, demos, earnings, streak, best time, onboarding ───

import re as _re
from urllib.parse import quote as _quote

_biz_tz = _tz.biz_tz   # kept for callers/tests that used the old private name


_PLACEHOLDER = _re.compile(r"\{(name|first_name|business|city|dgc)\}")


def render_template_text(body, lead, dgc):
    """Fill {name} {first_name} {business} {city} {dgc}. Plain substitution —
    unknown braces stay as typed and nothing in a template is ever evaluated."""
    name = (lead.name or "").strip()
    first = name.split(" ")[0] if name and not name.isdigit() else "there"
    vals = {
        "name": name or "there", "first_name": first,
        "business": lead.business or "your shop", "city": lead.city or "your city",
        "dgc": (getattr(dgc, "first_name", "") or getattr(dgc, "email", "") or "your ShopInADay team").strip(),
    }
    return _PLACEHOLDER.sub(lambda m: vals[m.group(1)], body or "")


def whatsapp_links(lead, dgc):
    """[(title, wa.me url, text)] for every active WhatsApp template."""
    from .models import MessageTemplate, TemplateKind
    if not lead.phone:
        return []
    out = []
    for t in MessageTemplate.objects.filter(kind=TemplateKind.WHATSAPP, is_active=True):
        text = render_template_text(t.body, lead, dgc)
        out.append({"title": t.title, "text": text, "url": f"https://wa.me/{lead.wa_number}?text={_quote(text)}"})
    return out


def scripts_for(lead, dgc):
    """Call scripts for this lead's stage (plus the ones for every stage)."""
    from .models import MessageTemplate, TemplateKind
    qs = MessageTemplate.objects.filter(kind=TemplateKind.SCRIPT, is_active=True).filter(
        Q(stage="") | Q(stage=lead.status))
    return [{"title": t.title, "text": render_template_text(t.body, lead, dgc)} for t in qs]


# -- demos ------------------------------------------------------------------

def book_demo(lead, when, *, actor):
    """Set (or clear, with ``when=None``) the demo time. Booking moves a lead
    that hasn't reached 'demo booked' yet there, and writes it into history."""
    if when is None:
        lead.demo_at = None
        lead.save(update_fields=["demo_at", "updated_at"])
        return lead
    lead.demo_at = when
    lead.save(update_fields=["demo_at", "updated_at"])
    if lead.status in (LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.INTERESTED):
        move_lead(lead, LeadStatus.DEMO_BOOKED, actor=actor, reason="demo scheduled")
    return lead


def demos_for(user):
    """(today's demos in time order, overdue demos still marked 'booked')."""
    tz = _biz_tz()
    now = timezone.now().astimezone(tz)
    start = dt.datetime.combine(now.date(), dt.time.min, tzinfo=tz)
    base = Lead.objects.filter(assigned_to=user, is_archived=False, status=LeadStatus.DEMO_BOOKED,
                               demo_at__isnull=False)
    today = list(base.filter(demo_at__gte=start, demo_at__lt=start + dt.timedelta(days=1)).order_by("demo_at"))
    overdue = list(base.filter(demo_at__lt=start).order_by("demo_at"))
    for l in today + overdue:
        l.demo_local = l.demo_at.astimezone(tz)
    return today, overdue


# -- earnings / streak / best time ------------------------------------------

def _monthly_commission_pct():
    from decimal import Decimal as D

    from apps.billing.models import Plan
    plan = Plan.objects.filter(is_active=True, is_public=True, price_monthly__gt=0).order_by("price_monthly").first()
    try:
        return D(plan.commission_pct_for("monthly")) if plan else D("20")
    except Exception:  # noqa: BLE001
        return D("20")


def svc_month_end(d):
    """Last calendar day of ``d``'s month."""
    return (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)


def earnings_preview(user):
    """What this DGC has earned this month, and what their pipeline could add
    (probability-weighted, using the configured average plan price)."""
    from decimal import Decimal as D

    from apps.billing.models import ManagerCommission
    cfg = CrmSettings.load()
    rate = _monthly_commission_pct() / D(100)
    today = biz_today()
    m_lo, m_hi = day_bounds(today.replace(day=1), svc_month_end(today))
    rows = ManagerCommission.objects.filter(manager=user, created_at__gte=m_lo,
                                            created_at__lt=m_hi).values("status").annotate(t=Sum("amount"))
    by = {r["status"]: (r["t"] or D("0")) for r in rows}
    earned = sum(by.values(), D("0"))
    stages = {r["status"]: r["n"] for r in Lead.objects.filter(
        assigned_to=user, is_archived=False, status__in=OPEN_LEAD_STATUSES).values("status").annotate(n=Count("id"))}
    expected = sum((D(stages.get(st, 0)) * D(cfg.probability(st)) / D(100) * cfg.avg_plan_price * rate
                    for st in stages), D("0"))
    hot = stages.get(LeadStatus.NEGOTIATING, 0) + stages.get(LeadStatus.DEMO_DONE, 0)
    return {"earned": earned.quantize(D("1")), "expected": expected.quantize(D("1")), "hot": hot,
            "per_win": (cfg.avg_plan_price * rate).quantize(D("1"))}


def _calls_by_local_day(user, days=60):
    from django.db.models.functions import TruncDate
    since = timezone.now() - dt.timedelta(days=days)
    rows = (Activity.objects.filter(actor=user, kind=ActivityKind.CALL, occurred_at__gte=since)
            .annotate(d=TruncDate("occurred_at", tzinfo=_biz_tz())).values("d").annotate(n=Count("id")))
    return {r["d"]: r["n"] for r in rows}


def call_streak(user, *, today=None):
    """Consecutive days (up to today) with at least min(10, daily target) calls.
    Today doesn't break the streak until the day is over."""
    target = targets_for(user).get(TargetMetric.CALLS, 40) or 40
    bar = min(10, target)
    per_day = _calls_by_local_day(user)
    day = today or biz_today()
    streak = 0
    if per_day.get(day, 0) >= bar:
        streak, day = 1, day - dt.timedelta(days=1)
    else:
        day -= dt.timedelta(days=1)                       # today still open: look back from yesterday
    while per_day.get(day, 0) >= bar:
        streak += 1
        day -= dt.timedelta(days=1)
    return {"days": streak, "bar": bar, "today": per_day.get(today or biz_today(), 0)}


def best_call_window(user, *, days=60, min_calls=30, min_bucket=8):
    """The 2-hour window where this DGC's calls connect most often, or None
    until there's enough data to say anything honest."""
    tz = _biz_tz()
    since = timezone.now() - dt.timedelta(days=days)
    calls = list(Activity.objects.filter(actor=user, kind=ActivityKind.CALL, occurred_at__gte=since)
                 .values_list("occurred_at", "outcome"))
    if len(calls) < min_calls:
        return None
    buckets = {}
    for when, outcome in calls:
        h = when.astimezone(tz).hour // 2 * 2
        n, ok = buckets.get(h, (0, 0))
        buckets[h] = (n + 1, ok + (1 if outcome == ActivityOutcome.CONNECTED else 0))
    ranked = [(ok / n, h, n) for h, (n, ok) in buckets.items() if n >= min_bucket and ok]
    if not ranked:
        return None
    rate, h, n = max(ranked)

    def fmt(x):
        x %= 24
        return f"{(x % 12) or 12} {'am' if x < 12 else 'pm'}"
    return {"label": f"{fmt(h)}–{fmt(h + 2)}", "pct": round(rate * 100), "calls": n}


# -- onboarding (derived from real activity; nothing to tick by hand) --------

def onboarding_steps(user):
    n_calls = Activity.objects.filter(actor=user, kind=ActivityKind.CALL).count()
    steps = [
        ("Log your first call", n_calls >= 1, "Open My day and tap 📞 Call on your first lead."),
        ("Reach 10 calls", n_calls >= 10, f"{min(n_calls, 10)}/10 so far — each tap-to-log counts."),
        ("Give your first demo",
         Activity.objects.filter(actor=user, kind=ActivityKind.DEMO).exists(),
         "Book one from a lead, then tap 🖥 Demo given."),
        ("Get trained by another DGC",
         TrainingLog.objects.filter(trainee=user, status=TrainingStatus.CONFIRMED).exists(),
         "Training → ＋ New entry → 'I was trained by…'."),
        ("Win your first store",
         Lead.objects.filter(assigned_to=user, status=LeadStatus.WON).exists(),
         "Use 'Deal won' on a lead — that creates the store."),
    ]
    return [{"label": a, "done": b, "hint": c} for a, b, c in steps]


# ── admin insights ─────────────────────────────────────────────────────────

import statistics as _stats

STAGE_ORDER = ["new", "contacted", "interested", "demo_booked", "demo_done", "negotiating", "won"]
_DEMO_REACHED = ("demo_booked", "demo_done", "negotiating", "won")


def _month_floor(d):
    return d.replace(day=1)


def _scoped(qs, user):
    """Limit a Lead queryset to one DGC's own leads (None = the whole team)."""
    return qs if user is None else qs.filter(assigned_to=user)


def source_roi(start, end, user=None):
    """Per lead source for leads created in [start, end]: funnel counts, win rate,
    and (if ad spend was entered) cost per lead / per won store. With ``user`` it
    covers only that DGC's leads and carries no spend (spend is a team figure)."""
    lo, hi = day_bounds(start, end)
    rows = {}
    leads = _scoped(Lead.objects.filter(created_at__gte=lo, created_at__lt=hi), user)
    for r in leads.values("source").annotate(
            n=Count("id"),
            touched=Count("id", filter=Q(first_touch_at__isnull=False)),
            demos=Count("id", filter=Q(status__in=_DEMO_REACHED)),
            won=Count("id", filter=Q(status=LeadStatus.WON))):
        label = r["source"] or "(no source)"
        rows[label] = {"source": label, "leads": r["n"], "touched": r["touched"], "demos": r["demos"], "won": r["won"],
                       "spend": Decimal("0")}
    from .models import SourceSpend
    spends = SourceSpend.objects.none() if user is not None else \
        SourceSpend.objects.filter(month__gte=_month_floor(start), month__lte=end)
    for sp in spends:
        row = rows.setdefault(sp.source, {"source": sp.source, "leads": 0, "touched": 0, "demos": 0, "won": 0,
                                          "spend": Decimal("0")})
        row["spend"] += sp.amount
    out = []
    for row in rows.values():
        row["win_pct"] = round(100 * row["won"] / row["leads"]) if row["leads"] else 0
        row["cost_per_lead"] = (row["spend"] / row["leads"]).quantize(Decimal("1")) if row["spend"] and row["leads"] else None
        row["cost_per_won"] = (row["spend"] / row["won"]).quantize(Decimal("1")) if row["spend"] and row["won"] else None
        out.append(row)
    return sorted(out, key=lambda r: (-r["leads"], r["source"]))


def funnel(user=None):
    """Open + won leads (lost excluded) as a cumulative funnel: how many got at
    least as far as each stage, the step conversion, and average days spent in
    the stage they're sitting in now. ``user`` = just that DGC's leads."""
    now = timezone.now()
    qs = _scoped(Lead.objects.filter(is_archived=False).exclude(status=LeadStatus.LOST), user)
    by = {st: [] for st in STAGE_ORDER}
    for st, changed, created in qs.values_list("status", "stage_changed_at", "created_at"):
        if st in by:
            by[st].append((now - (changed or created)).total_seconds() / 86400)
    counts = [len(by[st]) for st in STAGE_ORDER]
    reached = [sum(counts[i:]) for i in range(len(STAGE_ORDER))]
    rows = []
    for i, st in enumerate(STAGE_ORDER):
        prev = reached[i - 1] if i else None
        rows.append({
            "stage": st, "label": LeadStatus(st).label, "here": counts[i], "reached": reached[i],
            "step_pct": round(100 * reached[i] / prev) if prev else None,
            "avg_days": round(sum(by[st]) / len(by[st]), 1) if by[st] else None,
        })
    return rows


def stuck_leads(days=7, limit=10, user=None):
    """Open leads that have sat in the same stage for ``days``+ days (longest first)."""
    cut = timezone.now() - dt.timedelta(days=days)
    qs = (_scoped(Lead.objects.filter(is_archived=False, status__in=[s for s in OPEN_LEAD_STATUSES if s != LeadStatus.NEW]), user)
          .filter(Q(stage_changed_at__lt=cut) | Q(stage_changed_at__isnull=True, created_at__lt=cut))
          .select_related("assigned_to").order_by("stage_changed_at", "created_at")[:limit])
    now = timezone.now()
    out = list(qs)
    for l in out:
        l.stuck_days = (now - (l.stage_changed_at or l.created_at)).days
    return out


def forecast(user=None):
    """Expected new paying stores / MRR from the open pipeline, using the
    configured stage probabilities and average plan price. For one DGC it also
    gives the commission that would earn them per month."""
    cfg = CrmSettings.load()
    rows, stores = [], Decimal("0")
    counts = {r["status"]: r["n"] for r in _scoped(Lead.objects.filter(
        is_archived=False, status__in=OPEN_LEAD_STATUSES), user).values("status").annotate(n=Count("id"))}
    for st in STAGE_ORDER[:-1]:
        n = counts.get(st, 0)
        p = cfg.probability(st)
        exp = Decimal(n) * Decimal(p) / Decimal(100)
        stores += exp
        rows.append({"label": LeadStatus(st).label, "n": n, "pct": p, "expected": exp.quantize(Decimal("0.1"))})
    mrr = stores * cfg.avg_plan_price
    return {"rows": rows, "stores": stores.quantize(Decimal("0.1")),
            "mrr": mrr.quantize(Decimal("1")), "price": cfg.avg_plan_price,
            "commission": (mrr * _monthly_commission_pct() / Decimal(100)).quantize(Decimal("1"))}


def speed_to_lead(start, end, user=None):
    """How fast assigned leads get their first touch (minutes), plus the leads
    that have been waiting more than an hour right now."""
    lo, hi = day_bounds(start, end)
    mins = []
    for a, f in _scoped(Lead.objects.filter(assigned_at__gte=lo, assigned_at__lt=hi, first_touch_at__isnull=False),
                        user).values_list("assigned_at", "first_touch_at"):
        mins.append(max(0.0, (f - a).total_seconds() / 60))
    waiting = list(_scoped(Lead.objects.filter(
        assigned_to__isnull=False, first_touch_at__isnull=True, is_archived=False,
        status__in=OPEN_LEAD_STATUSES, assigned_at__lt=timezone.now() - dt.timedelta(hours=1),
        assigned_at__gte=timezone.now() - dt.timedelta(days=3)), user).select_related("assigned_to").order_by("assigned_at")[:10])
    now = timezone.now()
    for l in waiting:
        l.waiting_hours = int((now - l.assigned_at).total_seconds() // 3600)
    n = len(mins)
    return {
        "n": n, "median": round(_stats.median(mins)) if mins else None,
        "within_5": round(100 * sum(1 for m in mins if m <= 5) / n) if n else None,
        "within_30": round(100 * sum(1 for m in mins if m <= 30) / n) if n else None,
        "waiting": waiting,
    }


BURST_CALLS, BURST_MINUTES = 20, 10
ALL_CONNECTED_MIN, ALL_CONNECTED_PCT = 15, 95


def logging_flags(start, end):
    """Gentle sanity checks on manually-logged calls. These are prompts to have a
    conversation, not proof of anything: bursts of calls too fast to be real and
    suspiciously perfect connect rates."""
    lo, hi = day_bounds(start, end)
    calls = {}
    for actor, when, outcome in Activity.objects.filter(
            kind=ActivityKind.CALL, occurred_at__gte=lo, occurred_at__lt=hi
    ).order_by("actor", "occurred_at").values_list("actor", "occurred_at", "outcome"):
        calls.setdefault(actor, []).append((when, outcome))
    users = {u.pk: u for u in crm_people().filter(pk__in=calls)}
    flags = []
    window = dt.timedelta(minutes=BURST_MINUTES)
    for uid, rows in calls.items():
        user = users.get(uid)
        if user is None:
            continue
        times = [w for w, _ in rows]
        j, worst, at = 0, 0, None
        for i in range(len(times)):
            while times[i] - times[j] > window:
                j += 1
            if i - j + 1 > worst:
                worst, at = i - j + 1, times[j]
        if worst >= BURST_CALLS:
            flags.append({"user": user, "kind": "burst", "when": at,
                          "detail": f"{worst} calls logged within {BURST_MINUTES} minutes"})
        n = len(rows)
        ok = sum(1 for _, o in rows if o == ActivityOutcome.CONNECTED)
        if n >= ALL_CONNECTED_MIN and 100 * ok / n >= ALL_CONNECTED_PCT:
            flags.append({"user": user, "kind": "perfect", "when": None,
                          "detail": f"{ok} of {n} calls marked ‘spoke to them’ ({round(100 * ok / n)}%)"})
    return sorted(flags, key=lambda f: (person_label(f["user"]), f["kind"]))


def month_bounds(year, month):
    first = dt.date(year, month, 1)
    last = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    return first, last


def statement_rows(first, last):
    """One row per DGC for a month: activity, verified money, won stores, commission."""
    from apps.billing.models import ManagerCommission
    rows, totals = person_numbers(first, last, users=list(dgc_users()))
    lo, hi = day_bounds(first, last)
    won = {r["assigned_to"]: r["n"] for r in Lead.objects.filter(
        status=LeadStatus.WON, stage_changed_at__gte=lo, stage_changed_at__lt=hi).values("assigned_to").annotate(n=Count("id"))}
    com = {}
    for r in ManagerCommission.objects.filter(created_at__gte=lo, created_at__lt=hi).values("manager", "status").annotate(t=Sum("amount")):
        com.setdefault(r["manager"], {})[r["status"]] = r["t"] or Decimal("0")
    out = []
    for r in rows:
        c = com.get(r["user"].pk, {})
        out.append({**r, "won": won.get(r["user"].pk, 0),
                    "com_pending": c.get("pending", Decimal("0")), "com_approved": c.get("approved", Decimal("0")),
                    "com_paid": c.get("paid", Decimal("0")),
                    "com_total": sum(c.values(), Decimal("0"))})
    return out


# ── "what's possible" — the coaching numbers every DGC sees ─────────────────

from decimal import ROUND_HALF_UP as _HALF_UP


def inr(value):
    """₹ with Indian digit grouping: 123456 -> ₹1,23,456."""
    n = int(Decimal(value).quantize(Decimal(1), rounding=_HALF_UP))
    sign, digits = ("-" if n < 0 else ""), str(abs(n))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        digits = ",".join(parts + [tail])
    return f"{sign}₹{digits}"


def _nice(d):
    """1.0 -> '1', 2.4 -> '2.4' (one decimal at most)."""
    d = Decimal(d).quantize(Decimal("0.1"), rounding=_HALF_UP)
    return str(int(d)) if d == d.to_integral() else str(d)


def min_ticket():
    """The smallest monthly plan a client can pick (admin override, else the
    cheapest public paid plan) and what one such client pays the DGC each month."""
    from apps.billing.models import Plan
    cfg = CrmSettings.load()
    ticket = cfg.min_ticket_override
    if not ticket:
        plan = Plan.objects.filter(is_active=True, is_public=True, price_monthly__gt=0).order_by("price_monthly").first()
        ticket = plan.price_monthly if plan else Decimal("1499")
    pct = _monthly_commission_pct()
    per_month = (ticket * pct / Decimal(100)).quantize(Decimal("1"), rounding=_HALF_UP)
    return {"ticket": Decimal(ticket), "pct": pct, "per_month": per_month, "per_year": per_month * 12,
            "ticket_label": inr(ticket), "per_month_label": inr(per_month), "per_year_label": inr(per_month * 12)}


def possibility(user):
    """Everything the My-day 'what's possible' card needs, from the DGC's own
    plan (daily call target), their own open leads, and the funnel in settings.
    All simple multiplication — no promises, just what the funnel says."""
    cfg = CrmSettings.load()
    c1, c2, c3 = (Decimal(cfg.conv_call_to_demo), Decimal(cfg.conv_demo_to_trial), Decimal(cfg.conv_trial_to_paid))
    overall = c1 * c2 * c3 / Decimal(10000)                       # % of calls that end as a paid client
    per_call = overall / Decimal(100)                              # paid clients per call
    mt = min_ticket()
    per_client = Decimal(mt["per_month"])

    def step(calls):
        meets = calls * c1 / 100
        trials = meets * c2 / 100
        paid = trials * c3 / 100
        return meets, trials, paid

    per100 = [("Calls", Decimal(100)), ("Meet / demo", step(Decimal(100))[0]),
              ("Trials", step(Decimal(100))[1]), ("Paid clients", step(Decimal(100))[2])]

    target = Decimal(targets_for(user).get(TargetMetric.CALLS) or DEFAULT_TARGETS[TargetMetric.CALLS] or 40)
    meets, trials, paid = step(target)
    lo, hi = day_bounds(biz_today())
    done = Activity.objects.filter(actor=user, kind=ActivityKind.CALL, occurred_at__gte=lo, occurred_at__lt=hi).count()
    days = Decimal(cfg.working_days_month or 26)
    weeks = []
    for w in (1, 2, 3, 4):
        clients = target * days * Decimal(w) / 4 * per_call
        weeks.append({"week": w, "clients": clients, "clients_label": _nice(clients),
                      "monthly": clients * per_client, "monthly_label": inr(clients * per_client)})
    top = weeks[-1]["monthly"] or Decimal(1)
    for w in weeks:
        w["pct"] = max(8, int(w["monthly"] * 100 / top))

    stages = []
    counts = {r["status"]: r["n"] for r in Lead.objects.filter(
        assigned_to=user, is_archived=False, status__in=OPEN_LEAD_STATUSES).values("status").annotate(n=Count("id"))}
    total_open = sum(counts.values())
    exp_paid = Decimal(0)
    for st in STAGE_ORDER[:-1]:
        n = counts.get(st, 0)
        e = Decimal(n) * Decimal(cfg.probability(st)) / 100
        exp_paid += e
        stages.append({"stage": st, "label": LeadStatus(st).label, "n": n, "pct": cfg.probability(st),
                       "expected": e, "expected_label": _nice(e), "value_label": inr(e * per_client)})
    peak = max([r["n"] for r in stages] + [1])
    for r in stages:
        r["bar"] = max(4, int(r["n"] * 100 / peak)) if r["n"] else 0

    rows = [("Calls", target), ("Meet / demo", meets), ("Trials", trials), ("Paid clients", paid)]
    plan_rows = [{"label": l, "n": n, "n_label": _nice(n), "pct": max(7, int(n * 100 / target))}
                 for l, n in rows]
    return {
        "min": mt, "overall_pct": _nice(overall), "c": (int(c1), int(c2), int(c3)),
        "per100": [{"label": l, "n": _nice(n), "pct": max(7, int(n))} for l, n in per100],
        "plan": {"calls": int(target), "rows": plan_rows, "meets": _nice(meets), "trials": _nice(trials),
                 "paid": _nice(paid), "paid_n": paid, "monthly_label": inr(paid * per_client),
                 "yearly_label": inr(paid * per_client * 12)},
        "progress": {"done": done, "target": int(target), "pct": min(100, int(done * 100 / target)),
                     "left": max(0, int(target) - done), "paid_so_far": _nice(Decimal(done) * per_call),
                     "left_paid": _nice(Decimal(max(0, int(target) - done)) * per_call),
                     "value_so_far_label": inr(Decimal(done) * per_call * per_client)},
        "month": {"days": int(days), "weeks": weeks, "clients": weeks[-1]["clients_label"],
                  "monthly_label": weeks[-1]["monthly_label"]},
        "leads": {"stages": stages, "total": total_open, "expected": _nice(exp_paid),
                  "value_label": inr(exp_paid * per_client),
                  "untouched": Lead.objects.filter(assigned_to=user, is_archived=False, first_touch_at__isnull=True,
                                                   status__in=OPEN_LEAD_STATUSES).count()},
    }
