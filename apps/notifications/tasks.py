"""Async notification delivery — keeps email/SMS out of the request path.

``NotificationLog`` rows are created synchronously by the signal handler
(``apps.notifications.signals``) before this task is even queued, so a lost
broker message leaves a PENDING row -- not nothing -- for
``sweep_stuck_notifications_task`` to rescue."""

from celery import shared_task


@shared_task(name="apps.notifications.tasks.send_notification_task",
             acks_late=True, reject_on_worker_lost=True)
def send_notification_task(log_id):
    from .models import NotificationLog
    from .services import send_log

    log = NotificationLog.objects.filter(pk=log_id).first()
    if log is not None:
        send_log(log)


@shared_task(name="apps.notifications.tasks.sweep_stuck_notifications_task")
def sweep_stuck_notifications_task():
    from .services import sweep_stuck_notifications

    return len(sweep_stuck_notifications())
