"""Dashboard summary, daily roll-ups, and exportable reports."""

import csv
import io
import re
import time
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlparse

from django.core.cache import cache
from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import DailyMetric, EventCounter

REVENUE_STATUSES = ["confirmed", "processing", "packed", "shipped", "delivered"]


# --- counters ------------------------------------------------

def record_event(project, key, *, when=None, n=1):
    day = when or timezone.localdate()
    counter, _ = EventCounter.objects.get_or_create(project=project, date=day, key=key)
    EventCounter.objects.filter(pk=counter.pk).update(count=F("count") + n)


# --- live traffic (Visitors today / funnel / live visitors) --------------
#
# Storefront pages are anonymous and CDN-edge-cacheable (see
# apps.shopfront.middleware) -- a real visitor's request often never reaches
# Django at all once a page is cached, so these counters can ONLY be trusted
# when driven by a client-side JS beacon (apps.shopfront.views.BeaconView +
# static/shopfront/beacon.js), which always runs in the real browser
# regardless of where the HTML came from. add_to_cart / checkout_started are
# the exception -- those endpoints are never edge-cached (cart mutations,
# and "/checkout" is in _PRIVATE_PATHS), so they're recorded directly,
# server-side, from apps.shopfront.views.CartAddView / CheckoutView.

LIVE_VISITOR_TTL = 75  # seconds; beacon heartbeats every 25s, so 2 missed = gone


def mark_visitor_seen(project, vid, *, day=None):
    """First beacon of the day for this visitor id -> counts as a new
    "visitor" for the funnel and returns True (so the caller also records
    traffic source once, not on every page view)."""
    day = day or timezone.localdate()
    key = f"an:seen:{project.pk}:{day.isoformat()}:{vid}"
    is_new = cache.add(key, 1, timeout=60 * 60 * 26)
    if is_new:
        record_event(project, "visitor", when=day)
    return is_new


def record_page_event(project, event, path=None):
    record_event(project, "page_view")
    if event == "product_view":
        record_event(project, "product_view")
        if path:
            _record_product_view(project, path)


_PRODUCT_PATH_RE = re.compile(r"^/p/([^/]+)/?$")


def _record_product_view(project, path):
    """Per-product view count, keyed onto the same EventCounter table as
    every other beacon counter (see funnel_today's docstring for why this
    can only be beacon-driven) -- one row per product actually viewed that
    day, not one per project. Feeds top_product_views() below."""
    m = _PRODUCT_PATH_RE.match(path)
    if not m:
        return
    from apps.catalog.models import Product

    product_id = (
        Product.objects.filter(project=project, slug=m.group(1))
        .values_list("pk", flat=True).first()
    )
    if product_id:
        record_event(project, f"pv:{product_id}")


def top_product_views(project, limit=6):
    """Today's most-viewed products, ranked -- the "what's getting looked
    at" complement to Best sellers ("what's getting bought"). Same idea as
    the live-visitors widget (apps.analytics.services.live_visitors), just
    an aggregate instead of a live snapshot: what a beacon has been
    recording, not who's on the page this second."""
    from apps.catalog.models import Product

    today = timezone.localdate()
    rows = list(
        EventCounter.objects.filter(project=project, date=today, key__startswith="pv:")
        .order_by("-count")[:limit]
    )
    ids = []
    counts = {}
    for r in rows:
        try:
            pid = int(r.key[3:])
        except ValueError:
            continue
        ids.append(pid)
        counts[pid] = r.count
    titles = dict(Product.objects.filter(pk__in=ids).values_list("pk", "title"))
    return [
        {"product_id": pid, "title": titles[pid], "views": counts[pid]}
        for pid in ids if pid in titles
    ]


_TRAFFIC_BUCKETS = (
    ("search", ("google.", "bing.", "duckduckgo.", "yahoo.")),
    ("social", ("facebook.", "instagram.", "l.instagram.", "t.co", "twitter.",
                "x.com", "linkedin.", "pinterest.", "wa.me", "whatsapp.")),
)


def classify_referrer(referrer, own_host):
    """"direct" (no referrer, or the store's own domain), "search", "social",
    or "referral" (any other external site)."""
    if not referrer:
        return "direct"
    host = urlparse(referrer).netloc.lower()
    if not host or host == (own_host or "").lower():
        return "direct"
    for bucket, needles in _TRAFFIC_BUCKETS:
        if any(needle in host for needle in needles):
            return bucket
    return "referral"


def record_traffic_source(project, referrer, own_host):
    record_event(project, f"src_{classify_referrer(referrer, own_host)}")


def touch_live_visitor(project, vid, *, page, ip):
    """Upsert this visitor's presence (city, current page) with a rolling
    TTL -- read back by live_visitors(). Approximate by design (last-write-
    wins under concurrent beacons, no locking): this drives a vanity "who's
    browsing right now" widget, not billing or anything else load-bearing."""
    key = f"an:live:{project.pk}"
    now = time.time()
    data = cache.get(key) or {}
    data = {k: v for k, v in data.items() if now - v["ts"] < LIVE_VISITOR_TTL}
    data[vid] = {"page": page, "city": geoip_city(ip), "ts": now}
    cache.set(key, data, timeout=LIVE_VISITOR_TTL + 30)


def live_visitors(project):
    key = f"an:live:{project.pk}"
    now = time.time()
    data = cache.get(key) or {}
    return sorted(
        (v for v in data.values() if now - v["ts"] < LIVE_VISITOR_TTL),
        key=lambda v: -v["ts"],
    )


_geoip_reader = None
_geoip_unavailable = False

GEOIP_CACHE_TTL = 60 * 60 * 24 * 7  # a week -- an IP's city rarely changes


def geoip_city(ip):
    """City name for an IP. Tries the self-hosted GeoLite2-City .mmdb at
    settings.GEOIP_CITY_DB first (fast, no network, but needs the file --
    see that setting); when it's not present, falls back to a free
    no-signup IP-geolocation API (settings.GEOIP_EXTERNAL_LOOKUP, on by
    default) so the live-visitors widget works with zero setup. Every
    result (including a blank one) is cached a week per IP, so a given
    visitor's IP triggers at most one external call, not one per beacon.
    Always blank, never an error, on any failure."""
    if not ip:
        return ""
    from django.conf import settings

    cache_key = f"an:geoip:{ip}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    city = _geoip_local(ip)
    if not city and getattr(settings, "GEOIP_EXTERNAL_LOOKUP", True):
        city = _geoip_external(ip)
    cache.set(cache_key, city, timeout=GEOIP_CACHE_TTL)
    return city


def _geoip_local(ip):
    global _geoip_reader, _geoip_unavailable
    if _geoip_unavailable:
        return ""
    if _geoip_reader is None:
        import os

        from django.conf import settings

        path = getattr(settings, "GEOIP_CITY_DB", "")
        if not path or not os.path.exists(path):
            _geoip_unavailable = True
            return ""
        try:
            import geoip2.database

            _geoip_reader = geoip2.database.Reader(path)
        except Exception:  # noqa: BLE001 — any load failure just disables lookups
            _geoip_unavailable = True
            return ""
    try:
        return _geoip_reader.city(ip).city.name or ""
    except Exception:  # noqa: BLE001 — private/reserved/unresolvable IPs, etc.
        return ""


def _geoip_external(ip):
    """ipapi.co -- free, HTTPS, no signup/key, ~1k lookups/day/IP. Fine for
    this: results are cached a week per visitor IP, so real call volume is
    a small fraction of live-visitor traffic. A short timeout + broad
    except means a slow/blocked/rate-limited response just yields no city,
    never a hung or broken beacon request."""
    import json
    import urllib.request

    try:
        req = urllib.request.Request(
            f"https://ipapi.co/{ip}/json/",
            headers={"User-Agent": "sd-headless-backend (self-hosted storefront analytics)"},
        )
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode())
        return data.get("city") or ""
    except Exception:  # noqa: BLE001 — network/timeout/parse/rate-limit, etc.
        return ""


def funnel_today(project):
    """Visitors -> Product views -> Add to cart -> Checkout -> Orders, plus
    conversion rate and a traffic-source breakdown, for the dashboard's
    "today" widgets."""
    from apps.orders.models import Order

    today = timezone.localdate()
    counters = dict(
        EventCounter.objects.filter(project=project, date=today).values_list("key", "count")
    )
    visitors = counters.get("visitor", 0)
    orders = Order.objects.filter(
        project=project, status__in=REVENUE_STATUSES, created_at__date=today
    ).count()
    return {
        "visitors": visitors,
        "page_views": counters.get("page_view", 0),
        "product_views": counters.get("product_view", 0),
        "add_to_cart": counters.get("add_to_cart", 0),
        "checkout": counters.get("checkout_started", 0),
        "orders": orders,
        "conversion_rate": round(orders / visitors * 100, 1) if visitors else 0.0,
        "traffic_sources": {
            k[4:]: v for k, v in counters.items() if k.startswith("src_") and v
        },
        "live_visitors": live_visitors(project),
    }


# --- daily roll-up -----------------------------------------

def rebuild_daily(project, day=None):
    from apps.customers.models import Customer
    from apps.orders.models import Order, OrderItem

    day = day or timezone.localdate()
    orders = Order.objects.filter(project=project, created_at__date=day)
    revenue_orders = orders.filter(status__in=REVENUE_STATUSES)

    agg = revenue_orders.aggregate(n=Count("id"), rev=Sum("grand_total"))
    items = OrderItem.objects.filter(order__in=revenue_orders).aggregate(q=Sum("quantity"))
    cancelled = orders.filter(status__in=["cancelled", "failed"]).count()
    refunded = orders.filter(payment_status__in=["refunded", "partially_refunded"]).aggregate(
        a=Sum("grand_total")
    )["a"] or Decimal("0")

    new_cust = Customer.objects.filter(project=project, created_at__date=day).count()
    n = agg["n"] or 0
    rev = agg["rev"] or Decimal("0")

    metric, _ = DailyMetric.objects.update_or_create(
        project=project, date=day,
        defaults={
            "orders_count": n,
            "revenue": rev,
            "items_sold": items["q"] or 0,
            "new_customers": new_cust,
            "returning_customers": max(0, n - new_cust),
            "cancelled_count": cancelled,
            "refunded_amount": refunded,
            "aov": (rev / n) if n else Decimal("0"),
        },
    )
    return metric


# --- dashboard --------------------------------------------

def _sum_revenue(project, since):
    from apps.orders.models import Order

    return Order.objects.filter(
        project=project, status__in=REVENUE_STATUSES, created_at__date__gte=since
    ).aggregate(r=Sum("grand_total"))["r"] or Decimal("0")


def dashboard_summary(project):
    from apps.catalog.models import Product
    from apps.inventory import services as inv
    from apps.orders.models import Order, OrderItem

    today = timezone.localdate()
    week = today - timedelta(days=7)
    month = today - timedelta(days=30)

    all_revenue_orders = Order.objects.filter(project=project, status__in=REVENUE_STATUSES)
    total_rev = all_revenue_orders.aggregate(r=Sum("grand_total"))["r"] or Decimal("0")
    total_orders = all_revenue_orders.count()

    by_status = dict(
        Order.objects.filter(project=project).values_list("status").annotate(n=Count("id"))
    )

    best = (
        OrderItem.objects.filter(order__project=project, order__status__in=REVENUE_STATUSES)
        .values("product_id", "product_title")
        .annotate(qty=Sum("quantity"))
        .order_by("-qty")[:10]
    )

    return {
        "sales": {
            "today": _sum_revenue(project, today),
            "week": _sum_revenue(project, week),
            "month": _sum_revenue(project, month),
            "total": total_rev,
            "orders": total_orders,
            "aov": (total_rev / total_orders) if total_orders else Decimal("0"),
        },
        "orders_by_status": {
            s: by_status.get(s, 0)
            for s in ["pending", "confirmed", "processing", "packed", "shipped",
                      "delivered", "cancelled", "returned", "refunded"]
        },
        "customers": _customer_stats(project),
        "products": {
            "best_sellers": list(best),
            "low_stock": inv.low_stock_count(project),
            "out_of_stock": _out_of_stock(project),
            "total": Product.objects.filter(project=project).count(),
        },
        "revenue_series": _revenue_series(project, days=30),
    }


def _customer_stats(project):
    from apps.customers.models import Customer, Segment

    qs = Customer.objects.filter(project=project)
    return {
        "total": qs.count(),
        "new": qs.filter(segment=Segment.NEW).count(),
        "returning": qs.filter(segment=Segment.RETURNING).count(),
        "vip": qs.filter(segment=Segment.VIP).count(),
        "high_value": qs.filter(segment=Segment.HIGH_VALUE).count(),
        "inactive": qs.filter(segment=Segment.INACTIVE).count(),
    }


def revenue_chart_points(revenue_series):
    """"x,y x,y ..." for an inline SVG <polyline> (viewBox "0 0 300 100"),
    zero-filled so a quiet store still draws a flat line, not an empty chart."""
    if not revenue_series:
        return ""
    values = [float(pt["revenue"]) for pt in revenue_series]
    top = max(values) or 1.0
    n = len(values)
    step = 300 / (n - 1) if n > 1 else 0
    pts = []
    for i, v in enumerate(values):
        x = round(i * step, 1)
        y = round(96 - (v / top) * 90, 1)
        pts.append(f"{x},{y}")
    return " ".join(pts)


# Fixed palette so a status keeps the same colour across visits.
_STATUS_COLORS = {
    "pending": "#94a3b8", "confirmed": "#38bdf8", "processing": "#818cf8",
    "packed": "#a78bfa", "shipped": "#fb923c", "delivered": "#22c55e",
    "cancelled": "#ef4444", "returned": "#f43f5e", "refunded": "#eab308",
}


def status_donut_segments(orders_by_status):
    """SVG donut slices as stroke-dasharray/dashoffset on a shared circle
    (r=15.9155 -> circumference 100, so dasharray is directly a percentage).
    Empty when there are no orders at all."""
    total = sum(orders_by_status.values())
    if not total:
        return []
    segments = []
    offset = 0.0
    for status, n in orders_by_status.items():
        if not n:
            continue
        pct = round(n / total * 100, 2)
        segments.append({
            "status": status, "n": n, "pct": pct,
            "color": _STATUS_COLORS.get(status, "#cbd5e1"),
            "dasharray": f"{pct} {round(100 - pct, 2)}",
            "dashoffset": round(-offset, 2),
        })
        offset += pct
    return segments


def _out_of_stock(project):
    from django.db.models import F

    from apps.inventory.models import InventoryItem

    return InventoryItem.objects.filter(
        warehouse__project=project, quantity__lte=F("reserved")
    ).count()


def today_dashboard(project):
    """The store owner's landing page: today's numbers + what needs doing right
    now, on top of the same ``dashboard_summary`` the full Analytics report
    uses. Cheap — no new tables, just a couple of extra bounded queries."""
    from apps.inventory import services as inv
    from apps.orders.models import Order

    summary = dashboard_summary(project)

    needs_action_qs = Order.objects.filter(
        project=project, is_archived=False,
        status__in=["confirmed", "processing", "packed"],
        fulfillment_status__in=["unfulfilled", "partial"],
    )
    needs_action_count = needs_action_qs.count()
    needs_action = list(
        needs_action_qs.select_related("customer").order_by("created_at")[:8]
    )

    recent_orders = list(
        Order.objects.filter(project=project, is_archived=False)
        .select_related("customer").order_by("-created_at")[:6]
    )

    summary["needs_action"] = needs_action
    summary["needs_action_count"] = needs_action_count
    summary["recent_orders"] = recent_orders
    summary["low_stock_items"] = inv.low_stock_items(project)[:6]
    summary["funnel"] = funnel_today(project)
    # Same revenue-line + orders-by-status charts as the full Analytics
    # report (apps.control.phase11_views.AnalyticsView) -- the dashboard is
    # the one-page version, so it needs its own copy of these, not a link
    # to go look at them elsewhere.
    summary["revenue_points"] = revenue_chart_points(summary["revenue_series"])
    summary["status_donut"] = status_donut_segments(summary["orders_by_status"])
    summary["top_products_viewed"] = top_product_views(project)
    return summary


def _revenue_series(project, *, days=30):
    """One point per day in range, zero-filled -- a chart needs every day
    present to plot a real trend line, not just the days with a sale."""
    from apps.orders.models import Order

    start = timezone.localdate() - timedelta(days=days - 1)
    rows = (
        Order.objects.filter(project=project, status__in=REVENUE_STATUSES, created_at__date__gte=start)
        .annotate(d=TruncDate("created_at"))
        .values("d")
        .annotate(rev=Sum("grand_total"), n=Count("id"))
        .order_by("d")
    )
    by_day = {r["d"]: r for r in rows}
    return [
        {
            "date": d.isoformat(),
            "revenue": str((by_day.get(d) or {}).get("rev") or 0),
            "orders": (by_day.get(d) or {}).get("n") or 0,
        }
        for d in _iter_days(start, timezone.localdate())
    ]


# --- reports ----------------------------------------------

def _range(params):
    end = params.get("to") or timezone.localdate()
    start = params.get("from") or (end - timedelta(days=30))
    return start, end


def report(project, kind, params=None):
    params = params or {}
    fn = _REPORTS.get(kind)
    if fn is None:
        raise ValueError(f"Unknown report: {kind}")
    return fn(project, params)


def _sales_report(project, params):
    start, end = _range(params)
    for d in _iter_days(start, end):
        rebuild_daily(project, d)
    metrics = DailyMetric.objects.filter(project=project, date__gte=start, date__lte=end).order_by("date")
    return [
        {"date": m.date.isoformat(), "orders": m.orders_count, "revenue": str(m.revenue),
         "items_sold": m.items_sold, "aov": str(m.aov), "cancelled": m.cancelled_count,
         "refunded": str(m.refunded_amount)}
        for m in metrics
    ]


def _orders_report(project, params):
    from apps.orders.models import Order

    start, end = _range(params)
    qs = Order.objects.filter(project=project, created_at__date__gte=start, created_at__date__lte=end)
    return [
        {"number": o.number, "date": o.created_at.date().isoformat(), "status": o.status,
         "payment_status": o.payment_status, "email": o.email, "total": str(o.grand_total),
         "coupon": o.coupon_code}
        for o in qs.order_by("-created_at")
    ]


def _product_report(project, params):
    from apps.orders.models import OrderItem

    start, end = _range(params)
    rows = (
        OrderItem.objects.filter(
            order__project=project, order__status__in=REVENUE_STATUSES,
            order__created_at__date__gte=start, order__created_at__date__lte=end,
        )
        .values("sku", "product_title")
        .annotate(qty=Sum("quantity"), revenue=Sum("line_total"))
        .order_by("-qty")
    )
    return [{"sku": r["sku"], "product": r["product_title"], "quantity": r["qty"],
             "revenue": str(r["revenue"] or 0)} for r in rows]


def _customer_report(project, params):
    """Customers who ordered within the picked range (defaults to the last 30
    days, matching every other report). Was ignoring ``params`` entirely and
    returning the store's whole customer list regardless of the date filter
    shown on screen."""
    from apps.customers.models import Customer

    start, end = _range(params)
    qs = Customer.objects.filter(
        project=project, last_order_at__date__gte=start, last_order_at__date__lte=end,
    )
    return [
        {"email": c.email, "name": c.full_name, "segment": c.segment,
         "orders": c.orders_count, "total_spent": str(c.total_spent),
         "last_order": c.last_order_at.date().isoformat() if c.last_order_at else ""}
        for c in qs.order_by("-total_spent")
    ]


def _tax_report(project, params):
    from apps.orders.models import Order

    start, end = _range(params)
    qs = Order.objects.filter(
        project=project, status__in=REVENUE_STATUSES,
        created_at__date__gte=start, created_at__date__lte=end,
    )
    return [
        {"number": o.number, "date": o.created_at.date().isoformat(),
         "subtotal": str(o.subtotal), "tax": str(o.tax_total), "total": str(o.grand_total)}
        for o in qs.order_by("-created_at")
    ]


def _payment_report(project, params):
    from apps.payments.models import Payment

    start, end = _range(params)
    qs = Payment.objects.filter(
        project=project, created_at__date__gte=start, created_at__date__lte=end
    ).select_related("order")
    return [
        {"order": p.order.number, "provider": p.provider, "status": p.status,
         "amount": str(p.amount), "refunded": str(p.amount_refunded),
         "date": p.created_at.date().isoformat()}
        for p in qs.order_by("-created_at")
    ]


def _refund_report(project, params):
    from apps.payments.models import Refund

    start, end = _range(params)
    qs = Refund.objects.filter(
        payment__project=project, created_at__date__gte=start, created_at__date__lte=end
    ).select_related("payment", "payment__order")
    return [
        {"order": r.payment.order.number, "amount": str(r.amount), "status": r.status,
         "reason": r.reason, "date": r.created_at.date().isoformat()}
        for r in qs.order_by("-created_at")
    ]


def _inventory_report(project, params):
    from apps.inventory.models import InventoryItem

    return [
        {"product": i.product.title, "variant": (i.variant.name if i.variant else ""),
         "warehouse": i.warehouse.name, "on_hand": i.quantity, "reserved": i.reserved,
         "available": i.available, "threshold": i.low_stock_threshold, "low": i.is_low}
        for i in InventoryItem.objects.filter(warehouse__project=project)
        .select_related("product", "variant", "warehouse")
    ]


def _coupon_report(project, params):
    from apps.coupons.models import Coupon

    return [
        {"code": c.code, "type": c.discount_type, "value": str(c.value),
         "used": c.used_count, "limit": c.usage_limit or "",
         "total_discount": str(sum((r.amount for r in c.redemptions.all()), Decimal("0")))}
        for c in Coupon.objects.filter(project=project).prefetch_related("redemptions")
    ]


_REPORTS = {
    "sales": _sales_report,
    "orders": _orders_report,
    "product": _product_report,
    "customer": _customer_report,
    "tax": _tax_report,
    "payment": _payment_report,
    "refund": _refund_report,
    "inventory": _inventory_report,
    "coupon": _coupon_report,
}

REPORT_KINDS = list(_REPORTS)


def _iter_days(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def to_csv(rows):
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()
