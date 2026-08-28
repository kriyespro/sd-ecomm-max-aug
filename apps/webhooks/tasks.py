"""Async webhook delivery + periodic retry of failed deliveries."""

from celery import shared_task


@shared_task(name="apps.webhooks.tasks.deliver_event_task")
def deliver_event_task(project_id, event, data):
    from apps.projects.models import Project

    from .services import trigger

    project = Project.objects.filter(pk=project_id).first()
    if project is not None:
        trigger(project=project, event=event, data=data)


@shared_task(name="apps.webhooks.tasks.retry_due_task")
def retry_due_task():
    from .services import retry_due

    retry_due()
