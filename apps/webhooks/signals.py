"""Fan domain events out to subscribed webhook endpoints (async)."""

import json

from django.dispatch import receiver

from apps.core.events import domain_event


@receiver(domain_event)
def _on_domain_event(sender, event, project, payload, instance=None, **kwargs):
    if project is None:
        return
    from .tasks import deliver_event_task

    # coerce payload to JSON-safe primitives before it crosses the broker
    safe = json.loads(json.dumps(payload or {}, default=str))
    deliver_event_task.delay(project.id, event, safe)
