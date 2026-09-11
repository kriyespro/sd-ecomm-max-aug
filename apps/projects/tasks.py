"""Periodic project maintenance."""

from celery import shared_task


@shared_task(name="apps.projects.tasks.verify_pending_domains_task")
def verify_pending_domains_task():
    """Re-check DNS for every unverified domain — so a store owner who adds
    the TXT record late (even much later than the domain row itself) doesn't
    have to remember to hit "Verify" again. No age cutoff: a domain that
    fails DNS forever costs nothing but a cheap lookup every 5 minutes, and
    drops out of this query the moment it verifies."""
    from .models import Domain
    from . import domains as domain_svc

    pending = Domain.objects.filter(is_verified=False).order_by("created_at")[:200]
    for domain in pending:
        try:
            domain_svc.verify_domain(domain)
        except Exception:  # noqa: BLE001 — one bad lookup must not stop the batch
            continue
