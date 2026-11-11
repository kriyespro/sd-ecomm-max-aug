"""Periodic abandoned-cart recovery (Celery beat)."""

from celery import shared_task


@shared_task(name="apps.cart.tasks.send_abandoned_cart_emails_task")
def send_abandoned_cart_emails_task():
    from . import services

    sent = 0
    for cart in services.recoverable_carts():
        try:
            if _send_one(cart):
                sent += 1
        except Exception:  # noqa: BLE001 - one bad cart must never sink the batch
            import logging

            logging.getLogger(__name__).exception(
                "abandoned-cart recovery failed for cart %s", cart.pk,
            )
    return sent


def _send_one(cart):
    """Best-effort per cart — one bad cart (no domain yet, odd data) must
    never block the rest of the batch."""
    from apps.notifications.models import Event
    from apps.notifications.services import notify

    from . import services

    project = cart.project
    public_url = project.public_url
    if not public_url:
        return False  # nowhere to send them back to yet

    to = (cart.user.email if cart.user_id else "") or cart.email
    if not to:
        return False

    name = ""
    if cart.user_id:
        name = cart.user.get_full_name() or cart.user.get_username()

    context = {
        "name": name or "there",
        "store_name": project.name,
        "item_count": str(cart.item_count),
        "currency": project.currency,
        "total": str(cart.subtotal),
        "cart_url": public_url.rstrip("/") + "/cart/",
    }
    notify(project=project, event=Event.CART_ABANDONED, to=to, context=context, related=cart)

    phone, opt_in = services.cart_contact_phone(cart)
    if phone and opt_in:
        from apps.whatsapp.models import account_for

        if account_for(project) is not None:
            from apps.whatsapp.tasks import send_whatsapp_task

            send_whatsapp_task.delay(
                project.id, Event.CART_ABANDONED, phone, context, "cart", cart.pk,
            )

    services.mark_recovery_sent(cart)
    return True
