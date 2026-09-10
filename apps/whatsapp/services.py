"""Transactional WhatsApp sends. Best-effort — a failure is logged, never raised
into the caller. The 24h-window / text path is not used for transactional (the
customer rarely messaged first); everything goes out as an approved template.
"""

import logging

from .client import WhatsAppError, send_template
from .models import WAStatus, WhatsAppMessage, account_for

logger = logging.getLogger(__name__)

STOP_WORDS = {"stop", "unsubscribe", "cancel", "optout", "opt-out", "stop promotions"}


def wa_template_for(project, event):
    from apps.notifications.models import Channel, NotificationTemplate

    return (
        NotificationTemplate.objects
        .filter(project=project, event=event, channel=Channel.WHATSAPP, is_active=True)
        .exclude(wa_template_name="")
        .first()
    )


def send_transactional(*, project, event, to_number, context=None, related=None):
    context = context or {}
    account = account_for(project)
    msg = WhatsAppMessage(
        project=project, to_number=to_number or "", event=event,
        related_type=(related._meta.label_lower if related is not None else ""),
        related_id=(str(related.pk) if related is not None else ""),
    )

    if account is None or not account.ready:
        msg.status = WAStatus.SKIPPED
        msg.error = "No active WhatsApp account."
        msg.save()
        return msg
    if not to_number:
        msg.status = WAStatus.SKIPPED
        msg.error = "Customer has no phone number."
        msg.save()
        return msg

    tpl = wa_template_for(project, event)
    if tpl is None:
        msg.status = WAStatus.SKIPPED
        msg.error = "No WhatsApp template configured for this event."
        msg.save()
        return msg

    params = _params(tpl, context)
    msg.kind = "template"
    msg.template_name = tpl.wa_template_name
    msg.body_preview = " | ".join(params)[:255]

    try:
        wamid = send_template(
            account, to=to_number, name=tpl.wa_template_name,
            language=tpl.wa_language or "en_US", body_params=params,
        )
        msg.wamid = wamid
        msg.status = WAStatus.ACCEPTED
        if account.last_error:
            account.last_error = ""
            account.save(update_fields=["last_error"])
    except WhatsAppError as exc:
        msg.status = WAStatus.FAILED
        msg.error = str(exc)[:255]
        account.last_error = str(exc)[:255]
        account.save(update_fields=["last_error"])
        msg.save()
        if exc.retryable:
            raise
        return msg

    msg.save()
    return msg


def _params(tpl, context):
    out = []
    for line in (tpl.body or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(line.format(**context))
        except (KeyError, IndexError):
            out.append(line)
    return out


def record_inbound(account, *, from_number, wamid, text):
    """Log an inbound message; flag + honour opt-out keywords."""
    from apps.customers.models import Customer

    from .models import WhatsAppInbound

    is_opt_out = (text or "").strip().lower() in STOP_WORDS
    WhatsAppInbound.objects.create(
        account=account, from_number=from_number, wamid=wamid or "",
        text=text or "", is_opt_out=is_opt_out,
    )
    if is_opt_out:
        digits = "".join(c for c in from_number if c.isdigit())
        tail = digits[-10:]
        (Customer.objects
         .filter(project=account.project, phone__endswith=tail)
         .update(marketing_opt_in=False))
        logger.info("whatsapp opt-out from %s (project %s)", from_number, account.project_id)
