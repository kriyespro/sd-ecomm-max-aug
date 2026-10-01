"""Daily coach logic: today's missions, the store score, streak, last activity."""

import datetime as dt
from zoneinfo import ZoneInfo

from django.db.models import Count, Max, Q
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.timesince import timesince

from .checks import REGISTRY
from .models import Mission, MissionKind, MissionLog

# "Today" for a store owner is an Indian calendar day, same as the dashboard greeting.
STORE_TZ = ZoneInfo("Asia/Kolkata")


def local_today():
    return timezone.now().astimezone(STORE_TZ).date()


def _day_bounds(day):
    start = dt.datetime.combine(day, dt.time.min, tzinfo=STORE_TZ)
    return start, start + dt.timedelta(days=1)


def _active_missions(day):
    return Mission.objects.filter(is_active=True).filter(
        Q(starts_on__isnull=True) | Q(starts_on__lte=day),
        Q(ends_on__isnull=True) | Q(ends_on__gte=day),
    )


def resolve_cta(mission, project):
    """(label, url) for a mission's button, or ``None``."""
    url = (mission.cta_url or "").strip()
    label = (mission.cta_label or "").strip()
    reg = REGISTRY.get(mission.auto_key)
    if not url and reg:
        try:
            url = reverse(reg[3])
        except NoReverseMatch:
            url = ""
        label = label or reg[2]
    if "{store_url}" in url:
        store = project.public_url
        url = url.replace("{store_url}", store) if store else reverse("control:domains")
    if not url:
        return None
    # only same-site paths or http(s) links — never javascript: etc.
    if not (url.startswith("/") or url.startswith(("https://", "http://"))):
        return None
    return (label or "Open", url)


def sync_today(project, day=None, user=None):
    """Evaluate auto missions for ``day`` and return today's task rows
    ``[{mission, progress, target, done, cta}]`` (persisting the progress)."""
    day = day or local_today()
    start, end = _day_bounds(day)
    tasks = list(_active_missions(day).filter(kind=MissionKind.TASK))
    logs = {l.mission_id: l for l in MissionLog.objects.filter(project=project, day=day)}
    rows = []
    for m in tasks:
        log = logs.get(m.pk)
        if m.is_auto and m.auto_key in REGISTRY:
            count = int(REGISTRY[m.auto_key][1](project, start, end))
            target = max(1, m.target)
            if log is None:
                log = MissionLog(project=project, mission=m, day=day)
            changed = log.pk is None or log.progress != count
            log.progress = count
            if count >= target and not log.completed_at:
                log.completed_at, log.completed_by, changed = timezone.now(), None, True
            if changed:
                log.save()
            done, progress = bool(log.completed_at), min(count, target)
        else:
            target = 1
            done = bool(log and log.completed_at)
            progress = 1 if done else 0
        rows.append({
            "mission": m, "progress": progress, "target": target, "done": done,
            "cta": resolve_cta(m, project), "auto": m.is_auto and m.auto_key in REGISTRY,
        })
    return rows


def toggle_manual(project, mission, user, day=None):
    """Tick / untick an owner-checked task for today. Returns the new state."""
    day = day or local_today()
    if mission.kind != MissionKind.TASK or mission.is_auto:
        raise ValueError("not a manual task")
    log, _ = MissionLog.objects.get_or_create(project=project, mission=mission, day=day)
    if log.completed_at:
        log.completed_at, log.completed_by, log.progress = None, None, 0
    else:
        log.completed_at, log.completed_by, log.progress = timezone.now(), user, 1
    log.save()
    return bool(log.completed_at)


def streak_and_week(project, day=None):
    """Consecutive days (ending today, or yesterday if today isn't started yet)
    with at least one finished mission, plus a 7-day strip oldest -> today."""
    day = day or local_today()
    done_days = set(
        MissionLog.objects.filter(project=project, completed_at__isnull=False,
                                  day__gte=day - dt.timedelta(days=60), day__lte=day)
        .values_list("day", flat=True).distinct()
    )
    cursor = day if day in done_days else day - dt.timedelta(days=1)
    streak = 0
    while cursor in done_days:
        streak += 1
        cursor -= dt.timedelta(days=1)
    week = [
        {"day": day - dt.timedelta(days=i), "done": (day - dt.timedelta(days=i)) in done_days}
        for i in range(6, -1, -1)
    ]
    return streak, week


def last_activity(project):
    """Newest sign the owner/team worked on the store, or ``None``."""
    from apps.catalog.models import Product
    from apps.core.models import AuditLog
    from apps.coupons.models import Coupon

    stamps = [
        AuditLog.objects.filter(project=project, actor__isnull=False).aggregate(m=Max("created_at"))["m"],
        Product.objects.filter(project=project).aggregate(m=Max("updated_at"))["m"],
        Coupon.objects.filter(project=project).aggregate(m=Max("updated_at"))["m"],
        MissionLog.objects.filter(project=project, completed_at__isnull=False)
        .aggregate(m=Max("completed_at"))["m"],
    ]
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


def tip_of_the_day(day=None):
    day = day or local_today()
    tips = list(_active_missions(day).filter(kind=MissionKind.TIP).order_by("sort_order", "id"))
    return tips[day.toordinal() % len(tips)] if tips else None


def messages(day=None):
    return list(_active_missions(day or local_today()).filter(kind=MissionKind.MESSAGE))


# --- store score -------------------------------------------------------------

def _tier(score):
    if score >= 75:
        return "Star store"
    if score >= 55:
        return "Strong"
    if score >= 30:
        return "Growing"
    return "Just starting"


def store_score(project, *, week, last, setup_steps):
    """0-100 from four parts, each with its own bar and a plain-words hint:

    * Activity   (30) — days worked this week + how recently the owner was in
    * Sales      (30) — paid orders in the last 30 days
    * Catalogue  (20) — enough products, with photos, descriptions, categories
    * Setup      (20) — the owner onboarding steps finished (payments, domain…)
    """
    from apps.catalog.models import Product
    from apps.orders.models import Order

    now = timezone.now()

    days_active = sum(1 for d in week if d["done"])
    if last is None:
        recency = 0
    else:
        age = (now - last).days
        recency = 10 if age < 1 else 6 if age <= 3 else 3 if age <= 7 else 0
    activity = round(days_active / 7 * 20) + recency
    a_hint = ("Finish one task today to start a streak." if days_active == 0
              else "Keep your streak going — one task a day." if days_active < 5
              else "Great consistency.")

    paid = Order.objects.filter(
        project=project, payment_status="paid", created_at__gte=now - dt.timedelta(days=30)
    ).count()
    sales = 0 if paid == 0 else 8 if paid < 3 else 16 if paid < 10 else 24 if paid < 30 else 30
    s_hint = ("No paid orders in 30 days — share your store link and run an offer." if paid == 0
              else "Orders are coming in — an offer message can double them." if paid < 10
              else "Healthy sales.")

    products = Product.objects.filter(project=project, status="active")
    agg = products.aggregate(
        total=Count("id"),
        categorised=Count("id", filter=Q(category__isnull=False)),
        described=Count("id", filter=~Q(description="")),
    )
    total = agg["total"]
    if total:
        # 3+ photos is the bar for "photos done" — counted per product below
        rich = products.annotate(n=Count("images")).filter(n__gte=3).count()
        catalogue = (min(total, 10) / 10 * 8 + rich / total * 6
                     + agg["categorised"] / total * 3 + agg["described"] / total * 3)
        catalogue = round(catalogue)
        c_hint = ("Add more products — aim for 10+." if total < 10
                  else "Give every product 3+ photos." if rich < total
                  else "Catalogue looks great.")
    else:
        catalogue, c_hint = 0, "Add your first products with clear photos."

    done = sum(1 for s in setup_steps if s["done"])
    setup = round(done / len(setup_steps) * 20) if setup_steps else 20
    p_hint = ("Setup complete." if setup_steps and done == len(setup_steps)
              else next((f"Next: {s['label'].lower()}." for s in setup_steps if not s["done"]), ""))

    parts = [
        {"key": "activity", "label": "Activity", "score": activity, "max": 30, "hint": a_hint},
        {"key": "sales", "label": "Sales", "score": sales, "max": 30, "hint": s_hint},
        {"key": "catalogue", "label": "Catalogue", "score": catalogue, "max": 20, "hint": c_hint},
        {"key": "setup", "label": "Setup", "score": setup, "max": 20, "hint": p_hint},
    ]
    total_score = min(100, sum(p["score"] for p in parts))
    weakest = min(parts, key=lambda p: p["score"] / p["max"])
    return {
        "score": total_score, "tier": _tier(total_score), "parts": parts,
        "boost": weakest["hint"] if weakest["score"] < weakest["max"] else "",
        "boost_label": weakest["label"],
    }


def panel_context(project, user):
    from apps.control import quick_launch

    day = local_today()
    rows = sync_today(project, day, user)
    streak, week = streak_and_week(project, day)
    last = last_activity(project)
    score = store_score(project, week=week, last=last, setup_steps=quick_launch.owner_steps(project))
    done = sum(1 for r in rows if r["done"])
    tip = tip_of_the_day(day)
    return {
        "coach_rows": rows,
        "coach_done": done,
        "coach_total": len(rows),
        "coach_points": sum(r["mission"].points for r in rows if r["done"]),
        "coach_points_total": sum(r["mission"].points for r in rows),
        "coach_streak": streak,
        "coach_week": week,
        "coach_last": last,
        "coach_last_ago": timesince(last).split(",")[0] if last else "",
        "coach_score": score,
        "coach_tip": {"mission": tip, "cta": resolve_cta(tip, project)} if tip else None,
        "coach_messages": [{"mission": m, "cta": resolve_cta(m, project)} for m in messages(day)],
    }
