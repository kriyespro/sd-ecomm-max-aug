"""Periodic project maintenance."""

from datetime import timedelta

from celery import shared_task
from django.utils import timezone


@shared_task(name="apps.projects.tasks.verify_pending_domains_task")
def verify_pending_domains_task():
    """Re-check DNS for domains added in the last 14 days that aren't verified
    yet — so a store owner who adds the TXT record a bit late doesn't have to
    hit "Verify" again."""
    from .models import Domain
    from . import domains as domain_svc

    cutoff = timezone.now() - timedelta(days=14)
    pending = Domain.objects.filter(is_verified=False, created_at__gte=cutoff)[:200]
    for domain in pending:
        try:
            domain_svc.verify_domain(domain)
        except Exception:  # noqa: BLE001 — one bad lookup must not stop the batch
            continue
