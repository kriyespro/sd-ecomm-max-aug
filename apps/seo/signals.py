"""Ping IndexNow when a storefront-visible product is saved. Connected to
``Product`` post_save in ``SeoConfig.ready`` (avoids a load-time import cycle)."""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def product_saved(sender, instance, **kwargs):
    if not settings.SEO_INDEXNOW_KEY:
        return
    if getattr(instance, "status", "") != "active" or not getattr(instance, "search_indexed", True):
        return
    project = getattr(instance, "project", None)
    host = getattr(project, "public_host", "") if project else ""
    if not host:
        return
    from .tasks import indexnow_submit

    urls = [f"https://{host}/p/{instance.slug}/",
            f"https://{host}/", f"https://{host}/shop/"]
    try:
        indexnow_submit.delay(host, urls)
    except Exception:  # noqa: BLE001 - broker down must not break a save
        logger.debug("indexnow enqueue failed", exc_info=True)
