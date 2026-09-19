"""Periodic maintenance for Mission Control — emptying the Trash, daily
store backup snapshots."""

import logging
from datetime import timedelta

from celery import shared_task
from django.core.files.base import ContentFile
from django.utils import timezone

from .trash import TRASH_RETENTION_DAYS, purge_product

logger = logging.getLogger(__name__)


@shared_task(name="apps.control.tasks.purge_trashed_task")
def purge_trashed_task():
    """Hard-delete products and media assets trashed more than
    ``TRASH_RETENTION_DAYS`` ago."""
    from apps.catalog.models import Product
    from apps.media import services as media_svc
    from apps.media.models import MediaAsset

    cutoff = timezone.now() - timedelta(days=TRASH_RETENTION_DAYS)
    products = kept = 0
    for p in Product.objects.filter(trashed_at__isnull=False, trashed_at__lt=cutoff):
        if purge_product(p):
            products += 1
        else:
            kept += 1

    assets = 0
    for a in MediaAsset.objects.filter(trashed_at__isnull=False, trashed_at__lt=cutoff):
        try:
            media_svc.delete_asset(a)
            assets += 1
        except Exception:  # noqa: BLE001
            logger.exception("purge: media asset %s failed", a.pk)

    logger.info("trash purge: %s products, %s assets removed (%s products still cart-locked)",
                products, assets, kept)
    return {"products": products, "assets": assets, "kept": kept}


BACKUP_BATCH_SIZE = 50


@shared_task(name="apps.control.tasks.daily_store_backup_task")
def daily_store_backup_task():
    """One full backup (catalog/CMS/theme + orders/customers/payments) per
    active store that (a) is on a plan with allow_full_backup (Growth/Pro --
    apps.billing.limits.full_backup_allowed) and (b) has opted in
    (Project.feature_flags["auto_backup"], toggled from the "Automatic
    backup" checkbox on /admin/backup/, off by default). The plan check runs
    here too, not just at the checkbox, so a store downgraded off Growth/Pro
    stops getting backed up immediately rather than on its next toggle.
    Owner-only self-restore data -- see apps.control.models
    .StoreBackupSnapshot and apps.control.store_backup's own module
    docstring for why the sensitive half is safe here (never cross-store)
    and never offered to a DGC. Keeps a rolling RETENTION_DAYS-day window
    per store; a single store's failure (over the product/order cap, a
    transient storage error) never blocks the rest.

    Scheduled every 30 min from 12:00-5:30 AM IST (CELERY_BEAT_SCHEDULE) --
    each run only takes the next BACKUP_BATCH_SIZE stores that haven't been
    backed up yet today, so a large store count spreads across the window
    instead of everyone hitting storage/CPU at once at midnight."""
    from apps.billing.limits import full_backup_allowed
    from apps.control.models import RETENTION_DAYS, StoreBackupSnapshot
    from apps.control.store_backup import BackupError, dump_store
    from apps.projects.models import Project

    today = timezone.localdate()
    done_today = set(
        StoreBackupSnapshot.objects.filter(created_at__date=today)
        .values_list("project_id", flat=True)
    )
    batch = []
    projects = (
        Project.objects.filter(status=Project.Status.ACTIVE)
        .select_related("subscription__plan").order_by("pk").iterator()
    )
    for project in projects:
        if project.pk in done_today or not (project.feature_flags or {}).get("auto_backup"):
            continue
        if not full_backup_allowed(project):
            continue
        batch.append(project)
        if len(batch) >= BACKUP_BATCH_SIZE:
            break

    created = failed = 0
    for project in batch:
        try:
            blob = dump_store(project, include_sensitive=True)
        except BackupError as exc:
            logger.warning("daily backup: skipped %s: %s", project.pk, exc)
            failed += 1
            continue
        except Exception:  # noqa: BLE001
            logger.exception("daily backup: %s failed", project.pk)
            failed += 1
            continue

        snap = StoreBackupSnapshot(project=project, size_bytes=len(blob))
        slug = project.slug or f"store-{project.pk}"
        snap.archive.save(
            f"{slug}-{timezone.now():%Y%m%d-%H%M%S}.zip", ContentFile(blob), save=True,
        )
        created += 1

        cutoff = timezone.now() - timedelta(days=RETENTION_DAYS)
        for old in StoreBackupSnapshot.objects.filter(project=project, created_at__lt=cutoff):
            old.archive.delete(save=False)
            old.delete()

    logger.info("daily store backup: %s created, %s failed/skipped", created, failed)
    return {"created": created, "failed": failed}
