"""Payment orchestration.

Nothing here knows how a specific gateway works — that lives in
``apps.payments.providers``. This module owns: choosing an enabled provider,
creating Payment rows, verifying callbacks, consuming webhooks, capturing COD /
manual payments, and issuing refunds. Every settlement calls
``apps.orders.services.mark_paid`` so the order + inventory stay in sync.
"""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.crypto import get_random_string

from apps.core.events import Events, emit
from apps.core.models import AuditLog
from apps.core.services import record_audit
from apps.orders import services as orders

from .models import (
    Payment,
    PaymentEvent,
    PaymentProviderConfig,
    PaymentStatus,
    Provider,
    Refund,
    RefundStatus,
)
from .providers import ProviderError, get_provider_class


class PaymentError(Exception):
    pass


# --- provider selection ------------------------------------------

def enabled_provider_configs(project):
    return list(
        PaymentProviderConfig.objects.filter(project=project, is_enabled=True).order_by("priority", "provider")
    )


def get_provider(project, provider_key):
    try:
        config = PaymentProviderConfig.objects.get(project=project, provider=provider_key)
    except PaymentProviderConfig.DoesNotExist:
        raise PaymentError(f"{provider_key} is not configured for this store.")
    if not config.is_enabled:
        raise PaymentError(f"{provider_key} is disabled for this store.")
    return get_provider_class(provider_key)(config), config


def _log(payment, *, kind, project, provider="", signature_valid=None, payload=None, note=""):
    return PaymentEvent.objects.create(
        payment=payment,
        project=project,
        provider=provider or (payment.provider if payment else ""),
        kind=kind,
        signature_valid=signature_valid,
        payload=payload or {},
        note=note[:255],
    )


# --- initiate ----------------------------------------------------

@transaction.atomic
def initiate_payment(*, order, provider_key, actor=None, context=None):
    if order.payment_status == "paid":
        raise PaymentError("Order is already paid.")

    provider, config = get_provider(order.project, provider_key)

    payment = Payment.objects.create(
        project=order.project,
        order=order,
        provider=provider_key,
        amount=order.grand_total,
        currency=order.currency,
        status=PaymentStatus.CREATED,
        idempotency_key=get_random_string(24),
    )
    try:
        client_params = provider.start(payment, order, context=context)
    except ProviderError as exc:
        payment.status = PaymentStatus.FAILED
        payment.error_message = str(exc)[:255]
        payment.failed_at = timezone.now()
        payment.save(update_fields=["status", "error_message", "failed_at", "updated_at"])
        _log(payment, kind=PaymentEvent.Kind.ERROR, project=order.project, note=str(exc))
        raise PaymentError(str(exc)) from exc

    payment.status = PaymentStatus.PENDING
    payment.save(update_fields=["status", "updated_at"])
    _log(payment, kind=PaymentEvent.Kind.INITIATE, project=order.project, payload=client_params)
    record_audit(actor=actor, project=order.project, action=AuditLog.Action.CREATE, target=payment)
    return payment, client_params


# --- settle ----------------------------------------------------

@transaction.atomic
def _settle(payment, *, actor=None, reference="", event_kind=PaymentEvent.Kind.CAPTURE):
    payment.status = PaymentStatus.PAID
    payment.captured_at = timezone.now()
    payment.save(update_fields=["status", "captured_at", "provider_payment_id",
                                "provider_signature", "updated_at"])
    orders.mark_paid(order=payment.order, actor=actor,
                     reference=reference or payment.provider_payment_id or payment.provider)
    _log(payment, kind=event_kind, project=payment.project, note=reference)
    record_audit(actor=actor, project=payment.project, action=AuditLog.Action.UPDATE,
                 target=payment, changes={"status": PaymentStatus.PAID})
    order = payment.order
    payload = orders.order_event_payload(order, amount=str(payment.amount), provider=payment.provider)
    transaction.on_commit(
        lambda: emit(Events.PAYMENT_SUCCESS, project=payment.project, payload=payload, instance=payment)
    )
    return payment


@transaction.atomic
def verify_payment(*, payment, data, actor=None):
    """Validate the client-side callback and settle on success."""
    provider, _ = get_provider(payment.project, payment.provider)
    ok = provider.verify(payment, data)
    _log(payment, kind=PaymentEvent.Kind.VERIFY, project=payment.project,
         signature_valid=ok, payload={k: str(v) for k, v in (data or {}).items()})
    if not ok:
        payment.status = PaymentStatus.FAILED
        payment.error_message = "Signature verification failed."
        payment.failed_at = timezone.now()
        payment.save(update_fields=["status", "error_message", "failed_at", "updated_at"])
        payload = orders.order_event_payload(payment.order, provider=payment.provider,
                                             reason="signature_verification_failed")
        transaction.on_commit(
            lambda: emit(Events.PAYMENT_FAILED, project=payment.project, payload=payload, instance=payment)
        )
        raise PaymentError("Payment verification failed.")
    return _settle(payment, actor=actor, event_kind=PaymentEvent.Kind.VERIFY)


@transaction.atomic
def capture_payment(*, payment, actor=None, reference=""):
    """Mark a COD / manual / authorized payment as collected."""
    if payment.status == PaymentStatus.PAID:
        return payment
    return _settle(payment, actor=actor, reference=reference)


# --- COD / manual --------------------------------------------------

@transaction.atomic
def record_offline_payment(*, order, provider_key=Provider.COD, actor=None, mark_collected=False, reference=""):
    """Attach a COD/manual payment to an order.

    ``mark_collected=False`` (COD default): order is CONFIRMED now, cash captured
    later. ``mark_collected=True`` (manual): settle immediately.
    """
    provider, config = get_provider(order.project, provider_key)
    payment = Payment.objects.create(
        project=order.project, order=order, provider=provider_key,
        amount=order.grand_total, currency=order.currency,
        status=PaymentStatus.PENDING,
    )
    _log(payment, kind=PaymentEvent.Kind.INITIATE, project=order.project)
    if mark_collected:
        return _settle(payment, actor=actor, reference=reference)
    # COD: confirm the order without payment.
    if order.status == "pending":
        orders.transition_order(order=order, to_status="confirmed", actor=actor, note="COD order")
    record_audit(actor=actor, project=order.project, action=AuditLog.Action.CREATE, target=payment)
    return payment


# --- webhooks ----------------------------------------------------

@transaction.atomic
def handle_webhook(*, project, provider_key, headers, body: bytes):
    provider_class = get_provider_class(provider_key)
    try:
        config = PaymentProviderConfig.objects.get(project=project, provider=provider_key)
    except PaymentProviderConfig.DoesNotExist:
        raise PaymentError("Provider not configured.")
    result = provider_class(config).parse_webhook(headers, body)

    payment = None
    if result.provider_payment_id:
        payment = Payment.objects.filter(
            project=project, provider_payment_id=result.provider_payment_id
        ).first()
    if payment is None and result.provider_order_id:
        payment = Payment.objects.filter(
            project=project, provider_order_id=result.provider_order_id
        ).first()

    _log(payment, kind=PaymentEvent.Kind.WEBHOOK, project=project, provider=provider_key,
         signature_valid=result.signature_valid, payload=result.raw,
         note=result.event_type)

    if not result.signature_valid:
        raise PaymentError("Invalid webhook signature.")

    if payment is None:
        return None  # acknowledged, nothing to match

    etype = result.event_type
    if etype in {"payment.captured", "payment.authorized", "order.paid"}:
        if payment.status != PaymentStatus.PAID:
            if result.provider_payment_id:
                payment.provider_payment_id = result.provider_payment_id
            _settle(payment, reference=f"webhook:{etype}", event_kind=PaymentEvent.Kind.CAPTURE)
    elif etype == "payment.failed":
        payment.status = PaymentStatus.FAILED
        payment.failed_at = timezone.now()
        payment.save(update_fields=["status", "failed_at", "updated_at"])
    elif etype in {"refund.processed", "refund.created"}:
        _sync_refund_from_webhook(payment, result)
    return payment


def _sync_refund_from_webhook(payment, result):
    ref_id = result.provider_refund_id
    if ref_id and payment.refunds.filter(provider_refund_id=ref_id).exists():
        return
    # A refund we didn't originate (issued from the Razorpay dashboard).
    entity = result.raw.get("payload", {}).get("refund", {}).get("entity", {})
    amount = Decimal(entity.get("amount", 0)) / 100 if entity.get("amount") else payment.refundable_amount
    Refund.objects.create(
        payment=payment, amount=amount, status=RefundStatus.PROCESSED,
        provider_refund_id=ref_id, reason="Reconciled from webhook",
        meta=entity,
    )
    _apply_refund_totals(payment)


# --- refunds ----------------------------------------------------

def _apply_refund_totals(payment):
    total = sum(
        (r.amount for r in payment.refunds.filter(status=RefundStatus.PROCESSED)),
        Decimal("0"),
    )
    payment.amount_refunded = total
    if total <= 0:
        pass
    elif total >= payment.amount:
        payment.status = PaymentStatus.REFUNDED
    else:
        payment.status = PaymentStatus.PARTIALLY_REFUNDED
    payment.save(update_fields=["amount_refunded", "status", "updated_at"])


@transaction.atomic
def refund_payment(*, payment, amount=None, reason="", actor=None):
    if not payment.is_settled:
        raise PaymentError("Only a settled payment can be refunded.")
    amount = Decimal(amount) if amount is not None else payment.refundable_amount
    if amount <= 0 or amount > payment.refundable_amount:
        raise PaymentError("Refund amount is out of range.")

    provider, _ = get_provider(payment.project, payment.provider)
    refund = Refund.objects.create(
        payment=payment, amount=amount, reason=reason, actor=actor,
        status=RefundStatus.PENDING,
    )
    try:
        refund.provider_refund_id = provider.refund(payment, amount, reason=reason) or ""
        refund.status = RefundStatus.PROCESSED
    except ProviderError as exc:
        refund.status = RefundStatus.FAILED
        refund.meta = {"error": str(exc)}
        refund.save(update_fields=["status", "meta"])
        _log(payment, kind=PaymentEvent.Kind.ERROR, project=payment.project, note=str(exc))
        raise PaymentError(str(exc)) from exc
    refund.save(update_fields=["provider_refund_id", "status"])

    _apply_refund_totals(payment)
    _log(payment, kind=PaymentEvent.Kind.REFUND, project=payment.project,
         note=f"{amount} {payment.currency}")

    # Fully refunded -> move the order to REFUNDED when the machine allows it.
    if payment.status == PaymentStatus.REFUNDED and payment.order.status in {"delivered", "returned"}:
        try:
            orders.transition_order(order=payment.order, to_status="refunded", actor=actor,
                                    note="Full refund")
        except orders.OrderError:
            pass
    record_audit(actor=actor, project=payment.project, action=AuditLog.Action.UPDATE,
                 target=payment, changes={"refunded": str(amount)})
    payload = orders.order_event_payload(payment.order, amount=str(amount), provider=payment.provider)
    transaction.on_commit(
        lambda: emit(Events.PAYMENT_REFUNDED, project=payment.project, payload=payload, instance=payment)
    )
    return refund


# --- reconciliation ------------------------------------------------

def reconcile(project):
    """Return orders whose payment_status disagrees with their payments."""
    mismatches = []
    from apps.orders.models import Order

    for order in Order.objects.filter(project=project).prefetch_related("payments"):
        settled = any(p.status == PaymentStatus.PAID for p in order.payments.all())
        if settled and order.payment_status != "paid":
            mismatches.append((order, "payment settled but order not marked paid"))
        if not settled and order.payment_status == "paid":
            mismatches.append((order, "order marked paid but no settled payment"))
    return mismatches
