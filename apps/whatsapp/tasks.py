"""Async WhatsApp delivery — keeps the Cloud API call off the request path."""

import logging

from celery import shared_task

from .client import WhatsAppError

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.whatsapp.tasks.send_whatsapp_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_whatsapp_task(self, project_id, event, to_number, context,
                       related_label="", related_id=""):
    from django.apps import apps as django_apps

    from apps.projects.models import Project

    from .services import send_transactional

    project = Project.objects.filter(pk=project_id).first()
    if project is None:
        return "no-project"

    related = None
    if related_label and related_id:
        try:
            related = django_apps.get_model(related_label).objects.filter(pk=related_id).first()
        except Exception:  # noqa: BLE001
            related = None

    try:
        msg = send_transactional(project=project, event=event, to_number=to_number,
                                 context=context, related=related)
    except WhatsAppError as exc:
        raise self.retry(exc=exc)
    return msg.status
