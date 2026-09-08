"""Give every new store a trial subscription."""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_save, sender="projects.Project")
def _start_trial(sender, instance, created, **kwargs):
    if not created:
        return
    from .services import ensure_subscription

    try:
        ensure_subscription(instance)
    except Exception:  # noqa: BLE001 — never block store creation on billing
        # Callers that need the subscription (store provisioning, self-signup)
        # re-run ensure_subscription with an explicit plan and handle failure
        # themselves; here we only log so it isn't silent.
        logger.exception("trial subscription not created for project %s", instance.pk)
