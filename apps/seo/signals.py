"""Ping IndexNow when storefront-visible content is saved. Connected to
``Product``/``Page``/``Category`` post_save in ``SeoConfig.ready`` (avoids a
load-time import cycle). Was Product-only — a CMS page going live or a
category being renamed never reached Bing/Yandex/Seznam at all."""

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


def _host_for(project):
    return getattr(project, "public_host", "") if project else ""


def page_saved(sender, instance, **kwargs):
    if not settings.SEO_INDEXNOW_KEY:
        return
    if not getattr(instance, "is_live", False) or not getattr(instance, "show_in_sitemap", True):
        return
    host = _host_for(getattr(instance, "project", None))
    if not host:
        return
    from .tasks import indexnow_submit

    try:
        indexnow_submit.delay(host, [f"https://{host}/page/{instance.slug}/"])
    except Exception:  # noqa: BLE001
        logger.debug("indexnow enqueue failed", exc_info=True)


def category_saved(sender, instance, **kwargs):
    if not settings.SEO_INDEXNOW_KEY:
        return
    if not getattr(instance, "is_active", False):
        return
    host = _host_for(getattr(instance, "project", None))
    if not host:
        return
    from .tasks import indexnow_submit

    try:
        indexnow_submit.delay(host, [f"https://{host}/c/{instance.slug}/"])
    except Exception:  # noqa: BLE001
        logger.debug("indexnow enqueue failed", exc_info=True)
