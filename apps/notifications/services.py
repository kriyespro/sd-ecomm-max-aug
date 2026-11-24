"""Notification rendering + sending. Best-effort: a send failure is logged, never
raised into the caller's transaction.
"""

from .defaults import DEFAULTS
from .models import (
    Channel,
    NotificationLog,
    NotificationSettings,
    NotificationTemplate,
    SendStatus,
)
from .providers import email_provider, sms_provider


def _settings(project):
    obj, _ = NotificationSettings.objects.get_or_create(project=project)
    return obj


def render(project, event, *, channel=Channel.EMAIL, context=None):
    context = context or {}
    tpl = NotificationTemplate.objects.filter(
        project=project, event=event, channel=channel, is_active=True
    ).first()
    if tpl:
        subject, body = tpl.subject, tpl.body
    else:
        subject, body = DEFAULTS.get(event, ("Notification", "{event}"))
    try:
        subject = subject.format(**context)
        body = body.format(**context)
    except (KeyError, IndexError):
        pass  # leave unfilled placeholders rather than crash
    return subject, body


def create_log(*, project, event, to, context=None, channel=Channel.EMAIL, related=None):
    """Render + persist a ``NotificationLog`` row -- call this synchronously,
    before handing off to Celery. A PENDING row existing is what lets
    ``sweep_stuck_notifications`` ever find and resend it; creating it only
    inside the async task meant a lost broker message (worker killed
    mid-task, broker briefly unreachable) silently dropped the order
    confirmation / payment / shipment / refund / low-stock alert with zero
    trace and no way to notice, let alone retry."""
    settings_obj = _settings(project)
    log = NotificationLog(
        project=project, event=event, channel=channel, to_address=to or "",
        related_type=(f"{related._meta.label_lower}" if related is not None else ""),
        related_id=(str(related.pk) if related is not None else ""),
    )

    if not to:
        log.status = SendStatus.SKIPPED
        log.error = "No recipient."
        log.save()
        return log
    if not settings_obj.event_enabled(event):
        log.status = SendStatus.SKIPPED
        log.error = "Event disabled for this store."
        log.save()
        return log

    subject, body = render(project, event, channel=channel, context=context)
    log.subject, log.body = subject, body
    log.status = SendStatus.PENDING
    log.save()
    return log


def send_log(log):
    """Actually deliver an already-created PENDING log -- the async task's
    entry point. A no-op on a log that's already SENT/FAILED/SKIPPED, so a
    redelivered or re-swept task message is a safe no-op, not double send."""
    if log.status != SendStatus.PENDING:
        return log
    settings_obj = _settings(log.project)
    try:
        if log.channel == Channel.EMAIL:
            provider = email_provider(settings_obj.email_provider, settings_obj.email_config)
            from_address = settings_obj.from_email
            if settings_obj.from_name and from_address:
                from_address = f"{settings_obj.from_name} <{from_address}>"
            result = provider.send(to=log.to_address, subject=log.subject, body=log.body,
                                   from_address=from_address)
        else:
            provider = sms_provider(settings_obj.sms_provider, settings_obj.sms_config)
            result = provider.send(to=log.to_address, subject=log.subject, body=log.body)
        log.provider = result.get("provider", "")
        log.status = SendStatus.SENT
        log.meta = result
    except Exception as exc:  # noqa: BLE001 - best effort, capture everything
        log.status = SendStatus.FAILED
        log.error = str(exc)[:255]

    log.save()
    return log


def notify(*, project, event, to, context=None, channel=Channel.EMAIL, related=None):
    """Create + send in one synchronous step. Kept for direct/manual callers
    (tests, a resend action); the signal path uses ``create_log`` + the async
    task instead so creation survives a lost task message."""
    log = create_log(project=project, event=event, to=to, context=context,
                     channel=channel, related=related)
    if log.status == SendStatus.PENDING:
        send_log(log)
    return log


def sweep_stuck_notifications(older_than_minutes=10, limit=200):
    """Self-heal: a PENDING log (row created, never sent) older than this is
    one whose ``send_notification_task`` message got lost -- send it now."""
    from datetime import timedelta

    from django.utils import timezone

    cutoff = timezone.now() - timedelta(minutes=older_than_minutes)
    stuck = list(
        NotificationLog.objects.filter(status=SendStatus.PENDING, created_at__lt=cutoff)[:limit]
    )
    return [send_log(log) for log in stuck]
