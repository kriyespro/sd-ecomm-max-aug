"""Async webhook delivery + periodic retry of failed deliveries.

``WebhookDelivery`` rows are created synchronously by the signal handler
(``apps.webhooks.signals``) before this task is even queued, so a lost
broker message leaves a PENDING row -- not nothing -- for
``sweep_stuck_webhook_deliveries`` to rescue."""

from celery import shared_task


@shared_task(name="apps.webhooks.tasks.deliver_event_task",
             acks_late=True, reject_on_worker_lost=True)
def deliver_event_task(delivery_ids):
    from .services import attempt_deliveries

    attempt_deliveries(delivery_ids)


@shared_task(name="apps.webhooks.tasks.retry_due_task")
def retry_due_task():
    from .services import retry_due

    retry_due()


@shared_task(name="apps.webhooks.tasks.sweep_stuck_webhook_deliveries")
def sweep_stuck_webhook_deliveries():
    from .services import sweep_stuck_pending

    return len(sweep_stuck_pending())
