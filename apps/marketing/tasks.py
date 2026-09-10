"""Async delivery of server-side conversion events. Never called on the request
path directly — enqueued from signals so a slow/failed vendor call can't touch
checkout."""

import logging

from celery import shared_task

from . import capi, ga4, tiktok

logger = logging.getLogger(__name__)


def _row(integration_id):
    from .models import TrackingIntegration

    row = TrackingIntegration.objects.filter(pk=integration_id).first()
    if row is None or not row.server_ready:
        return None
    return row


@shared_task(
    name="apps.marketing.tasks.send_meta_capi_event",
    autoretry_for=(capi.CAPIError,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 3},
)
def send_meta_capi_event(*, integration_id, event_name, event_id, user_data,
                         custom_data=None, event_source_url="", test_event_code=""):
    row = _row(integration_id)
    if row is None:
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


@shared_task(
    name="apps.marketing.tasks.send_ga4_event",
    autoretry_for=(ga4.GA4Error,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 3},
)
def send_ga4_event(*, integration_id, name, event_id, params):
    row = _row(integration_id)
    if row is None:
        return "skipped"
    ga4.send_event(
        measurement_id=row.pixel_id,
        api_secret=row.server_token,
        name=name,
        params=params,
        client_id=ga4.client_id_for(event_id),
        test=bool(row.test_event_code),
    )
    logger.info("ga4 mp %s sent for project %s (event %s)",
                name, row.project_id, event_id)
    return "sent"


@shared_task(
    name="apps.marketing.tasks.send_tiktok_event",
    autoretry_for=(tiktok.TikTokError,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 3},
)
def send_tiktok_event(*, integration_id, event_name, event_id, contact,
                      properties=None, event_source_url=""):
    row = _row(integration_id)
    if row is None:
        return "skipped"
    tiktok.send_event(
        pixel_code=row.pixel_id,
        access_token=row.server_token,
        event_name=event_name,
        event_id=event_id,
        contact=contact,
        properties=properties,
        event_source_url=event_source_url,
        test_event_code=row.test_event_code,
    )
    logger.info("tiktok eapi %s sent for project %s (event %s)",
                event_name, row.project_id, event_id)
    return "sent"
