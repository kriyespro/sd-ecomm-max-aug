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
    configs = list(
        PaymentProviderConfig.objects.filter(project=project, is_enabled=True).order_by("priority", "provider")
    )
    if not any(c.provider == Provider.COD for c in configs):
        # COD needs no gateway credentials, so it's on by default until a store
        # explicitly adds a (disabled) config row to turn it off — same rule as
        # get_provider(). Keeps this list consistent with what checkout accepts.
        disabled = PaymentProviderConfig.objects.filter(
            project=project, provider=Provider.COD, is_enabled=False,
        ).exists()
        if not disabled:
            configs.append(PaymentProviderConfig(project=project, provider=Provider.COD, is_enabled=True))
            configs.sort(key=lambda c: (c.priority, c.provider))
    return configs


def cod_advance_plan(project):
    """``(gateway_provider_key, advance_amount)`` when this store requires a
    small online advance before a COD order is confirmed, else ``(None, 0)``
    -- either the store hasn't configured one, or no online provider is
    enabled to collect it with."""
    cod_config = PaymentProviderConfig.objects.filter(project=project, provider=Provider.COD).first()
    advance = cod_config.cod_advance_amount if cod_config else Decimal("0")
    if not advance or advance <= 0:
        return None, Decimal("0")
    enabled = [c for c in enabled_provider_configs(project) if c.provider not in (Provider.COD, Provider.MANUAL)]
    if not enabled:
        return None, Decimal("0")
    # Prefer a provider that verifies itself instantly (Razorpay/Stripe/PayU)
    # over direct UPI, which needs a staff member to manually confirm the
    # money arrived -- that defeats the point of a fast online advance. UPI
    # is picked only when it's the one online method the store has enabled.
    gateway = next((c for c in enabled if c.provider != Provider.UPI), enabled[0])
    return gateway.provider, advance


def get_provider(project, provider_key):
    try:
        config = PaymentProviderConfig.objects.get(project=project, provider=provider_key)
    except PaymentProviderConfig.DoesNotExist:
        if provider_key == Provider.COD:
            # COD needs no gateway credentials, so it's on by default until a
            # store explicitly adds a (disabled) config row to turn it off.
            config = PaymentProviderConfig(project=project, provider=Provider.COD, is_enabled=True)
        else:
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

def initiate_payment(*, order, provider_key, actor=None, context=None, amount=None):
    """Not wrapped in one big ``transaction.atomic()``: ``provider.start()`` is
    a live network call to the gateway (up to ~15s for Razorpay), and this is
    the highest-traffic payment entry point (every checkout attempt). Holding
    a DB transaction open for that long under real load risks exhausting the
    connection pool. Nothing here settles the order, so there's no
    double-settlement race to protect by keeping it atomic — only
    ``verify_payment``/``handle_webhook`` do that, and those stay atomic."""
    if order.payment_status == "paid":
        raise PaymentError("Order is already paid.")

    provider, config = get_provider(order.project, provider_key)

    payment = Payment.objects.create(
        project=order.project,
        order=order,
        provider=provider_key,
        amount=amount if amount is not None else order.grand_total,
        currency=order.currency,
        status=PaymentStatus.CREATED,
        idempotency_key=get_random_string(24),
    )
    try:
        client_params = provider.start(payment, order, context=context)
    except ProviderError as exc:
        with transaction.atomic():
            payment.status = PaymentStatus.FAILED
            payment.error_message = str(exc)[:255]
            payment.failed_at = timezone.now()
            payment.save(update_fields=["status", "error_message", "failed_at", "updated_at"])
            _log(payment, kind=PaymentEvent.Kind.ERROR, project=order.project, note=str(exc))
        raise PaymentError(str(exc)) from exc

    with transaction.atomic():
        payment.status = PaymentStatus.PENDING
        payment.save(update_fields=["status", "updated_at"])
        _log(payment, kind=PaymentEvent.Kind.INITIATE, project=order.project, payload=client_params)
        record_audit(actor=actor, project=order.project, action=AuditLog.Action.CREATE, target=payment)
    return payment, client_params


# --- settle ----------------------------------------------------

#: An order that reached either of these will never be fulfilled -- a
#: payment settling after the fact (a race between a customer-initiated
#: cancel and a staff capture, or a late webhook) must not resurrect it.
_NEVER_PAY_AFTER = frozenset({"cancelled", "failed"})


@transaction.atomic
def _settle(payment, *, actor=None, reference="", event_kind=PaymentEvent.Kind.CAPTURE):
    from apps.orders.models import Order

    # Lock the payment row before checking/flipping status -- without this, two
    # concurrent callers (a replayed client callback, or a webhook racing a
    # client verify) can both read PENDING and both run the settlement side
    # effects (loyalty credit, COD-remainder spawn) twice.
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.status == PaymentStatus.PAID:
        return payment
    # Locked once here and reused below -- a second Payment row on the same
    # order (e.g. an advance settling while a retry is also being verified)
    # must not independently pass the "no COD payment yet" check further down.
    order = Order.objects.select_for_update().get(pk=payment.order_id)
    if order.status in _NEVER_PAY_AFTER:
        raise PaymentError(f"Order {order.number} is {order.status} -- payment cannot be settled.")
    payment.status = PaymentStatus.PAID
    payment.captured_at = timezone.now()
    payment.save(update_fields=["status", "captured_at", "provider_payment_id",
                                "provider_signature", "updated_at"])
    orders.mark_paid(order=order, actor=actor,
                     reference=reference or payment.provider_payment_id or payment.provider)
    if order.cod_balance_due and order.cod_balance_due > 0 and not (
        order.payments.filter(provider=Provider.COD).exists()
    ):
        # Advance just settled -- spawn the COD remainder now, not at checkout
        # time, so a never-settled advance never leaves a stray COD Payment.
        record_offline_payment(order=order, provider_key=Provider.COD,
                               amount=order.cod_balance_due, actor=actor)
    _log(payment, kind=event_kind, project=payment.project, note=reference)
    record_audit(actor=actor, project=payment.project, action=AuditLog.Action.UPDATE,
                 target=payment, changes={"status": PaymentStatus.PAID})
    payload = orders.order_event_payload(order, amount=str(payment.amount), provider=payment.provider)
    transaction.on_commit(
        lambda: emit(Events.PAYMENT_SUCCESS, project=payment.project, payload=payload, instance=payment)
    )
    return payment


def verify_payment(*, payment, data, actor=None):
    """Validate the client-side callback and settle on success.

    Deliberately NOT @transaction.atomic at this level: on a bad-signature
    failure this saves Payment.FAILED and then raises PaymentError so the
    view can turn it into a 400 — wrapping the whole function would have
    rolled that save back along with the raise, silently leaving a failed
    verification looking exactly like an untouched PENDING payment (no
    failure record, no payment.failed event, nobody notified). The success
    path's own writes are still atomic via _settle()'s own decorator.
    """
    if payment.provider == Provider.UPI:
        # Never client-verifiable; a public callback must not even flip it to FAILED.
        raise PaymentError("UPI payments are confirmed by the store.")
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


def submit_upi_reference(*, payment, utr):
    """Shopper reports their UPI transaction reference. Stored for the owner to
    match against their bank/UPI app; never settles anything by itself."""
    from .providers.upi import UTR_RE

    utr = (utr or "").strip()
    if payment.provider != Provider.UPI or payment.status != PaymentStatus.PENDING:
        raise PaymentError("This payment can't take a reference.")
    if not UTR_RE.match(utr):
        raise PaymentError("Enter the UPI reference / UTR number (8–30 letters or digits).")
    clash = Payment.objects.filter(
        project=payment.project, provider=Provider.UPI, provider_payment_id__iexact=utr,
    ).exclude(pk=payment.pk).exists()
    if clash:
        raise PaymentError("That reference was already used on another order.")
    payment.provider_payment_id = utr
    payment.meta = {**(payment.meta or {}), "utr_submitted_at": timezone.now().isoformat()}
    payment.save(update_fields=["provider_payment_id", "meta", "updated_at"])
    _log(payment, kind=PaymentEvent.Kind.VERIFY, project=payment.project, note=f"UTR submitted: {utr}")
    return payment


@transaction.atomic
def capture_payment(*, payment, actor=None, reference=""):
    """Mark a COD / manual / authorized payment as collected."""
    if payment.status == PaymentStatus.PAID:
        return payment
    return _settle(payment, actor=actor, reference=reference)


# --- COD / manual --------------------------------------------------

@transaction.atomic
def record_offline_payment(*, order, provider_key=Provider.COD, actor=None, mark_collected=False,
                            reference="", amount=None):
    """Attach a COD/manual payment to an order.

    ``mark_collected=False`` (COD default): order is CONFIRMED now, cash captured
    later. ``mark_collected=True`` (manual): settle immediately.
    """
    provider, config = get_provider(order.project, provider_key)
    payment = Payment.objects.create(
        project=order.project, order=order, provider=provider_key,
        amount=amount if amount is not None else order.grand_total, currency=order.currency,
        status=PaymentStatus.PENDING,
    )
    _log(payment, kind=PaymentEvent.Kind.INITIATE, project=order.project)
    if mark_collected:
        return _settle(payment, actor=actor, reference=reference)
    if provider_key == Provider.UPI:
        # Direct UPI: order stays pending until staff confirm the money arrived.
        record_audit(actor=actor, project=order.project, action=AuditLog.Action.CREATE, target=payment)
        return payment
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

    # Locked for the rest of this (already-atomic) function: two near-
    # simultaneous deliveries of the same event must not both pass the
    # `status != PAID` check below and double-settle.
    payment = None
    if result.provider_payment_id:
        payment = Payment.objects.select_for_update().filter(
            project=project, provider_payment_id=result.provider_payment_id
        ).first()
    if payment is None and result.provider_order_id:
        payment = Payment.objects.select_for_update().filter(
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
            expected_minor = int((payment.amount * 100).to_integral_value())
            if result.amount_minor is not None and result.amount_minor < expected_minor:
                _log(payment, kind=PaymentEvent.Kind.ERROR, project=project, provider=provider_key,
                     note=f"amount mismatch: paid {result.amount_minor} < due {expected_minor}")
                raise PaymentError("Webhook amount does not match the order total.")
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

    # Keep Order.payment_status in sync -- previously only Payment.status was
    # updated here, leaving the storefront order page showing a stale "Paid"
    # badge after any refund.
    if total > 0:
        order = payment.order
        new_status = (
            orders.PaymentStatus.REFUNDED if total >= payment.amount
            else orders.PaymentStatus.PARTIALLY_REFUNDED
        )
        if order.payment_status != new_status:
            order.payment_status = new_status
            order.save(update_fields=["payment_status", "updated_at"])


@transaction.atomic
def refund_payment(*, payment, amount=None, reason="", actor=None):
    # Locked for the whole function (including the gateway call below): two
    # concurrent refund requests on the same payment must not both read the
    # same stale refundable_amount and both issue a real refund at the
    # provider. Refunds are a low-frequency staff action, not a request-per-
    # customer hot path, so holding the transaction during the network call
    # is an acceptable trade-off for closing that race.
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
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
    """Return orders whose payment_status disagrees with their payments.

    Compares the settled total against grand_total (not just "any payment
    settled") so a COD-advance order correctly sitting at PARTIALLY_PAID,
    with its remainder still PENDING, is never flagged -- only a genuine
    disagreement is.
    """
    from apps.orders.models import Order
    from apps.orders.models import PaymentStatus as OrderPaymentStatus

    settled_states = (
        OrderPaymentStatus.PAID, OrderPaymentStatus.PARTIALLY_REFUNDED, OrderPaymentStatus.REFUNDED,
    )
    mismatches = []
    for order in Order.objects.filter(project=project).prefetch_related("payments"):
        settled_total = sum(
            (p.amount for p in order.payments.all() if p.status == PaymentStatus.PAID), Decimal("0"),
        )
        if settled_total >= order.grand_total and order.payment_status not in settled_states:
            mismatches.append((order, "payment settled but order not marked paid"))
        elif Decimal("0") < settled_total < order.grand_total and order.payment_status not in (
            OrderPaymentStatus.PARTIALLY_PAID, *settled_states,
        ):
            mismatches.append((order, "advance settled but order not marked partially paid"))
        if settled_total <= 0 and order.payment_status == OrderPaymentStatus.PAID:
            mismatches.append((order, "order marked paid but no settled payment"))
    return mismatches
