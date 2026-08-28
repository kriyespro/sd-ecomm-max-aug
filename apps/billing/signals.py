"""Give every new store a trial subscription."""

from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender="projects.Project")
def _start_trial(sender, instance, created, **kwargs):
    if not created:
        return
    from .services import ensure_subscription

    try:
        ensure_subscription(instance)
    except Exception:  # noqa: BLE001 — never block store creation on billing
        pass
