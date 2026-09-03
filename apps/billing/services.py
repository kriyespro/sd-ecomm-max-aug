"""Subscription lifecycle: subscribe, change plan, invoice, collect, commission,
renew, suspend. Views and tasks call only these.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from . import razorpay
from .models import (
    BillingPeriod,
    BillingSettings,
    CommissionStatus,
    Invoice,
    InvoiceStatus,
    ManagerCommission,
    Plan,
    Subscription,
    SubscriptionStatus,
)


class BillingError(Exception):
    pass


# --- period maths --------------------------------------------------

def _period_end(start, period):
    if period == BillingPeriod.YEARLY:
        return start + timedelta(days=365)
    return start + timedelta(days=30)


def _next_invoice_number(prefix):
    year = timezone.now().year
    n = Invoice.objects.filter(number__startswith=f"{prefix}-{year}-").count() + 1
    return f"{prefix}-{year}-{n:06d}"


# --- subscribe / change plan -------------------------------------

@transaction.atomic
def ensure_subscription(project, *, plan=None, manager=None, trial_days=None):
    """Give a brand-new store a trial subscription. Idempotent.

    ``trial_days`` overrides ``BillingSettings.trial_days`` — used by public
    self-signup, which gets a shorter trial than a partner-provisioned store.
    """
    if hasattr(project, "subscription"):
        return project.subscription
    cfg = BillingSettings.load()
    plan = plan or Plan.objects.filter(is_active=True, is_public=True).order_by("sort_order").first()
    if plan is None:
        raise BillingError("No active plan to start a trial on.")
    now = timezone.now()
    days = cfg.trial_days if trial_days is None else trial_days
    trial_end = now + timedelta(days=days)
    return Subscription.objects.create(
        project=project, plan=plan, period=BillingPeriod.MONTHLY,
        status=SubscriptionStatus.TRIALING,
        current_period_start=now, current_period_end=trial_end, trial_end=trial_end,
        manager=manager,
    )


def reset_trial(subscription, days):
    """Re-length a still-running trial. Used right after self-signup, where the
    post_save signal has already created a standard-length trial."""
    if subscription.status != SubscriptionStatus.TRIALING:
        return subscription
    end = subscription.current_period_start + timedelta(days=days)
    subscription.trial_end = end
    subscription.current_period_end = end
    subscription.save(update_fields=["trial_end", "current_period_end", "updated_at"])
    return subscription


def is_dgc_managed(project) -> bool:
    """True when a platform manager (DGC) is credited for this store — they own
    the billing relationship, so the store's own team never sees plan & pricing."""
    sub = getattr(project, "subscription", None)
    return bool(sub and sub.manager_id)


@transaction.atomic
def change_plan(subscription, *, plan, period, actor=None):
    """Switch plan/period. Takes effect immediately; the price change applies to
    the next invoice (current paid period is honoured)."""
    if not plan.is_active:
        raise BillingError("That plan is not available.")
    subscription.plan = plan
    subscription.period = period
    subscription.cancel_at_period_end = False
    if subscription.status == SubscriptionStatus.CANCELLED:
        subscription.status = SubscriptionStatus.ACTIVE
    subscription.save(update_fields=["plan", "period", "cancel_at_period_end", "status", "updated_at"])
    # If they're out of trial and have no open invoice, bill the new plan now.
    if subscription.status != SubscriptionStatus.TRIALING and not _open_invoice(subscription):
        issue_invoice(subscription)
    return subscription


def cancel(subscription):
    subscription.cancel_at_period_end = True
    subscription.save(update_fields=["cancel_at_period_end", "updated_at"])
    return subscription


# --- invoicing ---------------------------------------------------

def _open_invoice(subscription):
    return subscription.invoices.filter(status=InvoiceStatus.OPEN).first()


@transaction.atomic
def issue_invoice(subscription, *, period_start=None):
    cfg = BillingSettings.load()
    existing = _open_invoice(subscription)
    if existing:
        return existing
    start = period_start or subscription.current_period_end
    end = _period_end(start, subscription.period)
    amount = subscription.current_price()
    inv = Invoice.objects.create(
        subscription=subscription,
        number=_next_invoice_number(cfg.invoice_prefix),
        period_start=start, period_end=end,
        description=f"{subscription.plan.name} — {subscription.get_period_display()}",
        amount=amount, currency=cfg.currency,
        due_at=timezone.now() + timedelta(days=cfg.grace_days),
    )
    if subscription.status == SubscriptionStatus.ACTIVE:
        subscription.status = SubscriptionStatus.PAST_DUE
        subscription.save(update_fields=["status", "updated_at"])
    return inv


def start_payment(invoice):
    """Return Razorpay checkout params for an open invoice."""
    if invoice.status != InvoiceStatus.OPEN:
        raise BillingError("This invoice is not open.")
    cfg = BillingSettings.load()
    res = razorpay.create_order(
        amount=invoice.amount, receipt=invoice.number,
        notes={"invoice": invoice.number, "project": str(invoice.subscription.project_id)},
        settings=cfg,
    )
    invoice.provider_order_id = res["order_id"]
    invoice.save(update_fields=["provider_order_id", "updated_at"])
    return res


@transaction.atomic
def confirm_payment(invoice, *, razorpay_payment_id, razorpay_signature):
    cfg = BillingSettings.load()
    synthetic = invoice.provider_order_id.startswith("order_test_")
    ok = synthetic or razorpay.verify_payment_signature(
        order_id=invoice.provider_order_id, payment_id=razorpay_payment_id,
        signature=razorpay_signature, secret=cfg.razorpay_key_secret,
    )
    if not ok:
        raise BillingError("Payment signature verification failed.")
    return mark_invoice_paid(invoice, provider_payment_id=razorpay_payment_id)


@transaction.atomic
def mark_invoice_paid(invoice, *, provider_payment_id=""):
    if invoice.status == InvoiceStatus.PAID:
        return invoice
    now = timezone.now()
    invoice.status = InvoiceStatus.PAID
    invoice.paid_at = now
    invoice.provider_payment_id = provider_payment_id
    invoice.save(update_fields=["status", "paid_at", "provider_payment_id", "updated_at"])

    sub = invoice.subscription
    sub.status = SubscriptionStatus.ACTIVE
    sub.current_period_start = invoice.period_start
    sub.current_period_end = invoice.period_end
    if sub.cancel_at_period_end:
        sub.status = SubscriptionStatus.CANCELLED
    sub.save(update_fields=["status", "current_period_start", "current_period_end", "updated_at"])

    _accrue_commission(invoice)
    return invoice


def _accrue_commission(invoice):
    sub = invoice.subscription
    if sub.manager_id is None or hasattr(invoice, "commission"):
        return
    rate = sub.plan.commission_pct_for(sub.period)
    amount = (invoice.amount * rate / Decimal("100")).quantize(Decimal("0.01"))
    if amount <= 0:
        return
    ManagerCommission.objects.create(
        manager_id=sub.manager_id, subscription=sub, invoice=invoice,
        period=sub.period, base_amount=invoice.amount, rate_pct=rate, amount=amount,
    )


# --- periodic ---------------------------------------------------

def issue_due_invoices(within_days=3):
    """Renewal invoices for subscriptions whose paid period ends soon."""
    cutoff = timezone.now() + timedelta(days=within_days)
    due = Subscription.objects.filter(
        status__in=[SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING],
        current_period_end__lte=cutoff,
        cancel_at_period_end=False,
    ).exclude(invoices__status=InvoiceStatus.OPEN)
    return [issue_invoice(s) for s in due]


def suspend_overdue():
    now = timezone.now()
    overdue = Invoice.objects.filter(status=InvoiceStatus.OPEN, due_at__lt=now).select_related("subscription")
    hit = []
    for inv in overdue:
        sub = inv.subscription
        if sub.status != SubscriptionStatus.SUSPENDED:
            sub.status = SubscriptionStatus.SUSPENDED
            sub.save(update_fields=["status", "updated_at"])
            hit.append(sub)
    return hit


# --- dashboards -----------------------------------------------

def platform_summary():
    from apps.orders.models import Order

    active = Subscription.objects.filter(status__in=[SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING])
    mrr = Decimal("0")
    for s in active.select_related("plan"):
        p = s.current_price()
        mrr += p if s.period == BillingPeriod.MONTHLY else (p / 12)

    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    revenue_month = Invoice.objects.filter(status=InvoiceStatus.PAID, paid_at__gte=month_start).aggregate(
        s=Sum("amount"))["s"] or Decimal("0")

    gmv = Order.objects.filter(payment_status="paid").aggregate(s=Sum("grand_total"))["s"] or Decimal("0")

    commissions_owed = ManagerCommission.objects.filter(
        status__in=[CommissionStatus.PENDING, CommissionStatus.APPROVED]
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")

    return {
        "mrr": mrr.quantize(Decimal("0.01")),
        "active_subs": active.count(),
        "revenue_month": revenue_month,
        "gmv": gmv,
        "commissions_owed": commissions_owed,
        "overdue_invoices": Invoice.objects.filter(status=InvoiceStatus.OPEN, due_at__lt=now).count(),
        "by_plan": list(
            active.values("plan__name").annotate(n=Count("id")).order_by("-n")
        ),
    }
