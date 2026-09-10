"""Async delivery of server-side conversion events. Never called on the request
path directly — enqueued from signals so a slow/failed Meta call can't touch
checkout."""

import logging

from celery import shared_task

from . import capi

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.marketing.tasks.send_meta_capi_event",
    autoretry_for=(capi.CAPIError,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 3},
)
def send_meta_capi_event(*, integration_id, event_name, event_id, user_data,
                         custom_data=None, event_source_url="", test_event_code=""):
    from .models import TrackingIntegration

    row = TrackingIntegration.objects.filter(pk=integration_id).first()
    if row is None or not row.server_ready:
        return "skipped"
    capi.send_event(
        pixel_id=row.pixel_id,
        access_token=row.server_token,
        event_name=event_name,
        event_id=event_id,
        user_data=user_data,
        custom_data=custom_data,
        event_source_url=event_source_url,
        test_event_code=test_event_code or row.test_event_code,
    )
    logger.info("meta capi %s sent for project %s (event %s)",
                event_name, row.project_id, event_id)
    return "sent"
