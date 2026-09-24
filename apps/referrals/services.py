"""Business logic for the per-store referral program -- kept out of the
views so both apps.shopfront (storefront) and apps.control (Mission
Control) can call the same functions."""

from django.utils import timezone

from .models import (
    CommissionStatus,
    ReferralAttribution,
    ReferralClick,
    ReferralCommission,
    ReferralProgram,
    Referrer,
    ReferrerStatus,
)


def get_or_create_program(project):
    obj, _ = ReferralProgram.objects.get_or_create(project=project)
    return obj


def active_program(project):
    """The project's program if the owner has turned it on, else None --
    every caller that gates on "is this feature live" uses this instead of
    a raw ``.filter(is_active=True)`` so the rule lives in one place."""
    return ReferralProgram.objects.filter(project=project, is_active=True).first()


def become_referrer(project, user):
    """Idempotent -- a customer who's already a referrer just gets their
    existing code and link back."""
    referrer, _ = Referrer.objects.get_or_create(project=project, user=user)
    return referrer


def referrer_for(project, user):
    if not user.is_authenticated:
        return None
    return Referrer.objects.filter(project=project, user=user).first()


def resolve_active_referrer(project, code):
    """A referrer whose link should actually count right now -- program on,
    referrer not disabled, code real. Used both to log a click and to
    attribute an order; returns ``None`` silently on anything invalid, since
    a bad/expired ``ref`` value must never break the page it's on."""
    if not code:
        return None
    if active_program(project) is None:
        return None
    return Referrer.objects.filter(
        project=project, code=code, status=ReferrerStatus.ACTIVE,
    ).first()


def record_click(project, code, landing_path=""):
    """Log a landing hit through a referral link. Returns the referrer (so
    the caller can set/refresh the attribution cookie) or ``None`` if the
    code didn't resolve to anything live."""
    referrer = resolve_active_referrer(project, code)
    if referrer is None:
        return None
    ReferralClick.objects.create(
        project=project, referrer=referrer, landing_path=landing_path[:200],
    )
    return referrer


def attribute_order(order, code):
    """Called right after an order is created at checkout. Writes who gets
    credit for it -- no commission yet, that only happens once the payment
    actually succeeds (see apps.referrals.signals). No-ops quietly on an
    invalid/expired code or an already-attributed order (re-POSTs, retries)."""
    referrer = resolve_active_referrer(order.project, code)
    if referrer is None:
        return None
    attribution, _ = ReferralAttribution.objects.get_or_create(
        order=order, defaults={"project": order.project, "referrer": referrer},
    )
    return attribution


def create_commission_for_payment(payment):
    """Payment succeeded -- if this order was attributed to a referrer,
    create its (Pending) commission. Idempotent: ``order`` is a OneToOne, so
    a retried/duplicate PAYMENT_SUCCESS (e.g. a webhook replay) can't double
    it. Returns the commission, or ``None`` if the order wasn't referred."""
    order = payment.order
    try:
        attribution = order.referral_attribution
    except ReferralAttribution.DoesNotExist:
        return None

    commission, _ = ReferralCommission.objects.get_or_create(
        order=order,
        defaults={
            "project": order.project,
            "referrer": attribution.referrer,
            "order_amount": order.grand_total,
            "commission_amount": get_or_create_program(order.project).commission_for(order.grand_total),
        },
    )
    return commission


_TRANSITIONS = {
    CommissionStatus.APPROVED: {CommissionStatus.PENDING},
    CommissionStatus.REJECTED: {CommissionStatus.PENDING, CommissionStatus.APPROVED},
    CommissionStatus.PAID: {CommissionStatus.APPROVED},
}


def set_commission_status(commission, new_status, *, actor=None):
    """Approve / reject / mark paid -- only the transitions in
    ``_TRANSITIONS`` are allowed (e.g. can't mark an already-Rejected
    commission Paid); anything else is a silent no-op so a stale/double
    button click in the admin screen never corrupts the record."""
    allowed_from = _TRANSITIONS.get(new_status)
    if allowed_from is None or commission.status not in allowed_from:
        return commission

    commission.status = new_status
    fields = ["status", "updated_at"]
    now = timezone.now()
    if new_status == CommissionStatus.APPROVED:
        commission.approved_at = now
        fields.append("approved_at")
    elif new_status == CommissionStatus.PAID:
        commission.paid_at = now
        fields.append("paid_at")
    commission.save(update_fields=fields)

    if actor is not None:
        from apps.core.models import AuditLog
        from apps.core.services import record_audit

        record_audit(
            actor=actor, project=commission.project, action=AuditLog.Action.UPDATE,
            target=commission, changes={"status": new_status},
        )
    return commission


def set_referrer_status(referrer, is_active, *, actor=None):
    new_status = ReferrerStatus.ACTIVE if is_active else ReferrerStatus.DISABLED
    if referrer.status == new_status:
        return referrer
    referrer.status = new_status
    referrer.save(update_fields=["status", "updated_at"])
    if actor is not None:
        from apps.core.models import AuditLog
        from apps.core.services import record_audit

        record_audit(
            actor=actor, project=referrer.project, action=AuditLog.Action.UPDATE,
            target=referrer, changes={"status": new_status},
        )
    return referrer
