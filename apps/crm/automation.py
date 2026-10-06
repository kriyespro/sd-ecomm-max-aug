"""Things the CRM does by itself (run from Celery beat, or "Run now" in settings).

Every job is idempotent: tasks carry an ``auto_key`` so a daily run never
creates the same task twice, and alerts/digests remember the day they were sent.
Each job is also failure-isolated per row — one bad store must never sink the batch.
"""

import datetime as dt
import logging

from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from apps.billing.models import Subscription, SubscriptionStatus

from . import services as svc
from .tz import biz_now, biz_today, to_biz
from .models import (
    Activity,
    CrmSettings,
    Lead,
    LeadStatus,
    OPEN_LEAD_STATUSES,
    Task,
    TaskStatus,
)

log = logging.getLogger(__name__)
User = get_user_model()


business_now = biz_now   # same clock as everywhere else in the CRM (apps/crm/tz.py)


# ── who owns a store's success ────────────────────────────────────────────

def responsible_dgc(project):
    """The DGC who should look after this store: whoever owns the CRM lead it
    came from, else its managing DGC, else the DGC whose link referred it."""
    lead = (Lead.objects.filter(converted_project=project, assigned_to__isnull=False,
                                assigned_to__is_active=True).order_by("-pk").first())
    if lead:
        return lead.assigned_to
    sub = getattr(project, "subscription", None)
    for user in (getattr(sub, "manager", None), getattr(sub, "referred_by", None)):
        if user is not None and user.is_active:
            return user
    return None


def _owner_phone(project):
    m = project.memberships.filter(role="owner", is_active=True).select_related("user__profile").first()
    return getattr(getattr(getattr(m, "user", None), "profile", None), "phone", "") if m else ""


def create_auto_task(*, key, title, detail, assignee, project=None, due_on=None):
    """Create a system task once per ``key`` (whatever became of the first one)."""
    if Task.objects.filter(auto_key=key).exists():
        return None
    return Task.objects.create(title=title[:200], detail=detail[:1000], assignee=assignee, project=project,
                               due_on=due_on or biz_today(), auto_key=key)


# ── 1) trial ending -> rescue call ────────────────────────────────────────

def trial_rescue():
    cfg = CrmSettings.load()
    if not cfg.trial_rescue_days:
        return 0
    now = timezone.now()
    subs = (Subscription.objects.filter(
        status=SubscriptionStatus.TRIALING, trial_end__isnull=False, is_comp=False,
        trial_end__gte=now, trial_end__lte=now + dt.timedelta(days=cfg.trial_rescue_days))
        .select_related("project", "manager", "referred_by"))
    made = 0
    for sub in subs:
        try:
            dgc = responsible_dgc(sub.project)
            if dgc is None:
                continue
            days = max(0, (sub.trial_end - now).days)
            phone = _owner_phone(sub.project)
            when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
            if create_auto_task(
                    key=f"trial:{sub.pk}:{to_biz(sub.trial_end).date()}",
                    title=f"Trial ends {when}: {sub.project.name} — call to convert",
                    detail=f"Their free trial ends {when}. Ask how it's going, fix anything blocking them, "
                           f"and help them pick a plan.{(' Phone: ' + phone) if phone else ''}",
                    assignee=dgc, project=sub.project, due_on=biz_today()):
                made += 1
        except Exception:  # noqa: BLE001
            log.exception("crm: trial rescue failed for subscription %s", sub.pk)
    return made


# ── 2) store health -> nudge ──────────────────────────────────────────────

def store_health():
    """A store with no products after N days, or live-but-no-orders after M days,
    gets a task for the DGC who looks after it."""
    from apps.catalog.models import Product
    from apps.orders.models import Order

    cfg = CrmSettings.load()
    now = timezone.now()
    made = 0
    live = (Subscription.objects.filter(status__in=[SubscriptionStatus.TRIALING, SubscriptionStatus.ACTIVE])
            .select_related("project", "manager", "referred_by"))
    for sub in live:
        try:
            project = sub.project
            age = (now - project.created_at).days
            dgc = None
            if cfg.health_no_products_days and age >= cfg.health_no_products_days:
                if not Product.objects.filter(project=project).exists():
                    dgc = dgc or responsible_dgc(project)
                    if dgc and create_auto_task(
                            key=f"noprod:{project.pk}",
                            title=f"{project.name} has no products yet ({age} days old)",
                            detail="Help them add their first products — an empty store can't sell. "
                                   "Offer to do the product entry (request store work if needed).",
                            assignee=dgc, project=project):
                        made += 1
                    continue
            if (cfg.health_no_orders_days and age >= cfg.health_no_orders_days
                    and Product.objects.filter(project=project).exists()
                    and not Order.objects.filter(project=project).exists()):
                dgc = responsible_dgc(project)
                month = now.strftime("%Y-%m")
                if dgc and create_auto_task(
                        key=f"noorders:{project.pk}:{month}",
                        title=f"{project.name}: no orders after {age} days",
                        detail="Their store is set up but hasn't had an order. Check pixels/WhatsApp/social "
                               "sharing and help them promote it.",
                        assignee=dgc, project=project):
                    made += 1
        except Exception:  # noqa: BLE001
            log.exception("crm: store health failed for subscription %s", sub.pk)
    return made


# ── 3) lead recycling ─────────────────────────────────────────────────────

def recycle_leads():
    """Return cold leads nobody has touched for N days to the unassigned pool so
    an admin can share them out again."""
    cfg = CrmSettings.load()
    if not cfg.recycle_days:
        return 0
    cutoff = timezone.now() - dt.timedelta(days=cfg.recycle_days)
    recent = Activity.objects.filter(lead=OuterRef("pk"), occurred_at__gte=cutoff)
    today = biz_today()
    qs = (Lead.objects.filter(assigned_to__isnull=False, is_archived=False,
                              status__in=[LeadStatus.NEW, LeadStatus.CONTACTED], created_at__lt=cutoff)
          .annotate(has_recent=Exists(recent))
          .filter(has_recent=False)
          .filter(Q(next_follow_up__isnull=True) | Q(next_follow_up__lt=today)))
    n = 0
    for lead in qs[:500]:
        note = f"[returned to pool: no contact in {cfg.recycle_days} days]"
        lead.assigned_to = lead.assigned_by = None
        lead.auto_assigned = False
        lead.notes = f"{lead.notes}\n{note}".strip()
        lead.save(update_fields=["assigned_to", "assigned_by", "auto_assigned", "notes", "updated_at"])
        n += 1
    return n


# ── 4) admin alerts ───────────────────────────────────────────────────────

def admin_emails(cfg=None):
    cfg = cfg or CrmSettings.load()
    listed = [e.strip() for e in cfg.digest_emails.replace("\n", ",").split(",") if e.strip()]
    if listed:
        return listed
    qs = User.objects.filter(is_active=True).exclude(email="").filter(
        Q(is_superuser=True) | Q(profile__platform_role="platform_owner"))
    return sorted(set(qs.values_list("email", flat=True)))


def quiet_dgcs(now=None):
    """Active DGCs with no logged activity today (business-timezone day)."""
    n = business_now(now)
    today, tz = n.date(), n.tzinfo
    lo = dt.datetime.combine(today, dt.time.min, tzinfo=tz)
    hi = lo + dt.timedelta(days=1)
    active = set(Activity.objects.filter(occurred_at__gte=lo, occurred_at__lt=hi).values_list("actor_id", flat=True))
    return [u for u in svc.dgc_users() if u.pk not in active]


def quiet_alert(now=None):
    """After the configured hour, email admins which DGCs have done nothing yet.
    At most once a day. Sundays are skipped."""
    cfg = CrmSettings.load()
    now = business_now(now)
    if (not cfg.quiet_check or now.hour < cfg.quiet_after_hour or now.weekday() == 6
            or cfg.last_quiet_on == now.date()):
        return 0
    quiet = quiet_dgcs(now)
    cfg.last_quiet_on = now.date()
    cfg.save(update_fields=["last_quiet_on", "updated_at"])
    to = admin_emails(cfg)
    if not quiet or not to:
        return 0
    names = "\n".join(f"  • {svc.person_label(u)}" for u in quiet)
    send_mail(f"CRM: {len(quiet)} DGC(s) quiet today",
              f"No activity logged yet today by:\n{names}\n\nA quick nudge now saves the day.\n", None, to, fail_silently=True)
    return len(quiet)


def weekly_digest(now=None):
    """Monday-morning team summary for the last 7 days. Once a week."""
    cfg = CrmSettings.load()
    now = business_now(now)
    if not cfg.weekly_digest or now.weekday() != 0 or now.hour < 8 or cfg.last_digest_on == now.date():
        return 0
    end = now.date() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=6)
    rows, totals = svc.person_numbers(start, end, users=list(svc.dgc_users()))   # the calling team only
    rows = sorted(rows, key=lambda r: (-r["calls"], -r["demos"], r["name"]))
    pipe = svc.pipeline_counts()
    lines = [f"CRM weekly summary · {start:%d %b} – {end:%d %b}", "",
             f"Calls {totals['calls']} ({totals['connected']} spoke, {totals['connect_pct']}%) · "
             f"Demos {totals['demos']} · Trained {totals['trained']} · "
             f"Collected ₹{totals['collection']} · Won {pipe['won']} total", ""]
    for r in rows[:10]:
        lines.append(f"  {r['name']}: {r['calls']} calls, {r['demos']} demos, ₹{r['collection']}")
    quiet_week = [r["name"] for r in rows if not (r["calls"] or r["demos"])]
    if quiet_week:
        lines += ["", "No activity all week: " + ", ".join(quiet_week)]
    lines += ["", f"Pipeline: " + " › ".join(f"{lab} {n}" for _, lab, n in pipe["stages"]),
              f"Unassigned: {pipe['unassigned']}"]
    cfg.last_digest_on = now.date()
    cfg.save(update_fields=["last_digest_on", "updated_at"])
    to = admin_emails(cfg)
    if not to:
        return 0
    send_mail(f"CRM weekly summary · {start:%d %b}–{end:%d %b}", "\n".join(lines) + "\n", None, to, fail_silently=True)
    return 1


def run_all():
    """Everything once, for the 'Run now' button. Returns {job: count}."""
    return {"trial_rescue": trial_rescue(), "store_health": store_health(),
            "recycled": recycle_leads(), "quiet_alert": quiet_alert(), "weekly_digest": weekly_digest()}
