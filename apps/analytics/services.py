"""Dashboard summary, daily roll-ups, and exportable reports."""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

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


def _out_of_stock(project):
    from django.db.models import F

    from apps.inventory.models import InventoryItem

    return InventoryItem.objects.filter(
        warehouse__project=project, quantity__lte=F("reserved")
    ).count()


def _revenue_series(project, *, days=30):
    from apps.orders.models import Order

    start = timezone.localdate() - timedelta(days=days - 1)
    rows = (
        Order.objects.filter(project=project, status__in=REVENUE_STATUSES, created_at__date__gte=start)
        .annotate(d=TruncDate("created_at"))
        .values("d")
        .annotate(rev=Sum("grand_total"), n=Count("id"))
        .order_by("d")
    )
    return [{"date": r["d"].isoformat(), "revenue": str(r["rev"] or 0), "orders": r["n"]} for r in rows]


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
