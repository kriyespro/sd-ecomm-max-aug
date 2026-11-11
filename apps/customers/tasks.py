"""Owner-triggered WhatsApp broadcast to a customer segment (Celery). One
task per click of the "Send" button on the admin segments screen -- not a
periodic task like abandoned-cart recovery, same loop shape though."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="apps.customers.tasks.send_segment_broadcast_task")
def send_segment_broadcast_task(project_id, segment, message):
    from apps.notifications.models import Event
    from apps.projects.models import Project
    from apps.whatsapp.models import account_for
    from apps.whatsapp.tasks import send_whatsapp_task

    from .models import Customer

    project = Project.objects.filter(pk=project_id).first()
    if project is None or account_for(project) is None:
        return 0

    sent = 0
    customers = (
        Customer.objects.filter(project=project, segment=segment, marketing_opt_in=True)
        .exclude(phone="")
    )
    for customer in customers:
        try:
            context = {
                "name": customer.first_name or "there",
                "store_name": project.name,
                "message": message,
            }
            send_whatsapp_task.delay(
                project.id, Event.MARKETING_BROADCAST, customer.phone, context,
                "customers.customer", customer.pk,
            )
            sent += 1
        except Exception:  # noqa: BLE001 - one bad customer must never sink the batch
            logger.exception("segment broadcast failed to queue for customer %s", customer.pk)
    return sent
