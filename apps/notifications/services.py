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


def notify(*, project, event, to, context=None, channel=Channel.EMAIL, related=None):
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

    try:
        if channel == Channel.EMAIL:
            provider = email_provider(settings_obj.email_provider, settings_obj.email_config)
            from_address = settings_obj.from_email
            if settings_obj.from_name and from_address:
                from_address = f"{settings_obj.from_name} <{from_address}>"
            result = provider.send(to=to, subject=subject, body=body, from_address=from_address)
        else:
            provider = sms_provider(settings_obj.sms_provider, settings_obj.sms_config)
            result = provider.send(to=to, subject=subject, body=body)
        log.provider = result.get("provider", "")
        log.status = SendStatus.SENT
        log.meta = result
    except Exception as exc:  # noqa: BLE001 - best effort, capture everything
        log.status = SendStatus.FAILED
        log.error = str(exc)[:255]

    log.save()
    return log
