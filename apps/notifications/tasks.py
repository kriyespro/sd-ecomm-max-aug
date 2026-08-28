"""Async notification delivery — keeps email/SMS out of the request path."""

from celery import shared_task


@shared_task(name="apps.notifications.tasks.send_notification_task")
def send_notification_task(project_id, notif_event, to, context,
                           related_label="", related_id=""):
    from django.apps import apps as django_apps

    from apps.projects.models import Project

    from .services import notify

    project = Project.objects.filter(pk=project_id).first()
    if project is None:
        return

    related = None
    if related_label and related_id:
        try:
            related = django_apps.get_model(related_label).objects.filter(pk=related_id).first()
        except Exception:  # noqa: BLE001
            related = None

    notify(project=project, event=notif_event, to=to, context=context, related=related)
