"""Publish a product -> queue an auto-share to every connected channel.

Fires once per (product, channel): ``SocialPost`` has a unique constraint and
``share_product`` is a no-op once a post is ``POSTED``, so re-saving a product
never re-posts it.
"""

import logging

logger = logging.getLogger(__name__)


def product_saved(sender, instance, created, **kwargs):
    if getattr(instance, "status", "") != "active" or not getattr(instance, "search_indexed", True):
        return
    project = getattr(instance, "project", None)
    if project is None:
        return

    from .models import SocialPost, auto_share_targets

    targets = auto_share_targets(project)
    if not targets:
        return

    already = set(
        SocialPost.objects
        .filter(project=project, product=instance)
        .values_list("provider", flat=True)
    )
    from .tasks import share_product_task

    for account in targets:
        if account.provider in already:
            continue
        try:
            share_product_task.delay(account.pk, instance.pk)
        except Exception:  # noqa: BLE001 - broker down must not break the save
            logger.debug("social share enqueue failed", exc_info=True)
