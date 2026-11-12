"""Self-serve return/exchange request flow for a delivered order.

A customer requests a return within ``RETURN_WINDOW_DAYS`` of delivery; the
owner approves (choosing how to refund) or rejects. Approving a RETURN (not
EXCHANGE -- a replacement ships separately, out of scope here) is a single
step: "I accept this and I'm refunding it", matching the platform's existing
trust-the-owner convention for COD's "mark collected" button -- there is no
separate "confirm physical receipt" step.
"""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.orders import services as orders_svc
from apps.orders.models import OrderStatus, OrderStatusEvent
from apps.projects.services import owner_notification_email, owner_notification_phone

from .models import RefundMethod, RequestKind, RequestStatus, ReturnRequest

RETURN_WINDOW_DAYS = 7


class ReturnError(Exception):
    pass


def delivered_at(order):
    event = (
        order.events.filter(kind=OrderStatusEvent.Kind.STATUS, to_value=OrderStatus.DELIVERED)
        .order_by("-created_at").first()
    )
    return event.created_at if event else None


def request_return(*, order, customer, kind, reason, note=""):
    if order.status != OrderStatus.DELIVERED:
        raise ReturnError("Only a delivered order can be returned or exchanged.")

    delivered = delivered_at(order)
    if delivered is None or timezone.now() - delivered > timezone.timedelta(days=RETURN_WINDOW_DAYS):
        raise ReturnError(f"The return window ({RETURN_WINDOW_DAYS} days after delivery) has passed.")

    if ReturnRequest.objects.filter(order=order).exists():
        raise ReturnError("A return/exchange request already exists for this order.")

    reason = (reason or "").strip()
    if not reason:
        raise ReturnError("Enter a reason for the return or exchange.")

    req = ReturnRequest.objects.create(
        project=order.project, order=order, customer=customer,
        kind=kind, reason=reason, note=note,
    )

    _notify_owner_requested(req)
    return req


def _notify_owner_requested(req):
    from apps.notifications.models import Event
    from apps.notifications.services import notify

    project = req.order.project
    context = {
        "order_number": req.order.number, "kind": req.get_kind_display(), "reason": req.reason,
    }
    owner_email = owner_notification_email(project)
    if owner_email:
        notify(project=project, event=Event.RETURN_REQUESTED, to=owner_email, context=context, related=req)

    phone = owner_notification_phone(project)
    if phone:
        from apps.whatsapp.models import account_for

        if account_for(project) is not None:
            from apps.whatsapp.tasks import send_whatsapp_task

            send_whatsapp_task.delay(
                project.id, Event.RETURN_REQUESTED, phone, context, "returns.returnrequest", req.pk,
            )


def _notify_customer(req, *, event, resolution_note=""):
    from apps.notifications.models import Event as NotifyEvent
    from apps.notifications.services import notify

    to = req.order.email
    if not to:
        return
    context = {
        "name": req.order.customer.first_name if req.order.customer and req.order.customer.first_name else "there",
        "order_number": req.order.number, "kind": req.get_kind_display(),
        "resolution_note": resolution_note,
    }
    notify(project=req.order.project, event=event, to=to, context=context, related=req)


@transaction.atomic
def approve_return(*, request, refund_method, amount=None, actor=None):
    if request.status != RequestStatus.REQUESTED:
        raise ReturnError("This request has already been resolved.")

    order = request.order
    customer = request.customer
    resolved_amount = Decimal(amount) if amount is not None else order.grand_total

    if refund_method == RefundMethod.STORE_CREDIT:
        if customer is None:
            raise ReturnError("No customer on this order to credit.")
        from apps.credits import services as credits_svc

        credits_svc.issue_return_credit(
            project=order.project, customer=customer, amount=resolved_amount, order=order,
            note=f"Return refund for order {order.number}",
        )
    elif refund_method == RefundMethod.GATEWAY_REFUND:
        from apps.payments import services as payments_svc
        from apps.payments.models import Payment

        payment = (
            Payment.objects.filter(order=order).order_by("-created_at").first()
        )
        if payment is None or not payment.is_settled:
            raise ReturnError("No settled payment found to refund.")
        payments_svc.refund_payment(payment=payment, amount=resolved_amount, reason=request.reason, actor=actor)
    elif refund_method == RefundMethod.MANUAL:
        pass  # owner has already handled it off-platform (bank transfer, cash)
    else:
        raise ReturnError("Choose a refund method.")

    request.status = RequestStatus.APPROVED
    request.resolved_refund_method = refund_method
    request.resolved_amount = resolved_amount
    request.resolved_by = actor
    request.resolved_at = timezone.now()
    request.save(update_fields=[
        "status", "resolved_refund_method", "resolved_amount", "resolved_by", "resolved_at", "updated_at",
    ])

    if request.kind == RequestKind.RETURN:
        # A gateway refund above may already have moved the order straight to
        # REFUNDED (apps.payments.services.refund_payment's own delivered/
        # returned -> refunded shortcut) -- refresh before checking, since the
        # in-memory `order` loaded before that call is otherwise stale.
        order.refresh_from_db()
        try:
            orders_svc.transition_order(order=order, to_status=OrderStatus.RETURNED, actor=actor,
                                        note="Return request approved")
        except orders_svc.OrderError:
            pass  # already in a terminal state -- not fatal
        orders_svc.restock_returned_order(order=order, actor=actor)

    from apps.notifications.models import Event

    _notify_customer(request, event=Event.RETURN_APPROVED)
    return request


@transaction.atomic
def reject_return(*, request, note, actor=None):
    if request.status != RequestStatus.REQUESTED:
        raise ReturnError("This request has already been resolved.")

    request.status = RequestStatus.REJECTED
    request.resolved_note = note or ""
    request.resolved_by = actor
    request.resolved_at = timezone.now()
    request.save(update_fields=["status", "resolved_note", "resolved_by", "resolved_at", "updated_at"])

    from apps.notifications.models import Event

    _notify_customer(request, event=Event.RETURN_REJECTED, resolution_note=request.resolved_note)
    return request
