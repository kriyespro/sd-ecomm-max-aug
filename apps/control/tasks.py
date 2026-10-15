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
        for old in StoreBackupSnapshot.objects.filter(project=project, created_at__lt=cutoff, label=""):
            old.archive.delete(save=False)
            old.delete()

    logger.info("daily store backup: %s created, %s failed/skipped", created, failed)
    return {"created": created, "failed": failed}


@shared_task(name="apps.control.tasks.run_store_restore_task",
            time_limit=1800, soft_time_limit=1740)
def run_store_restore_task(job_id: int):
    """Do the actual wipe + rebuild for a :class:`StoreRestoreJob` -- see
    that model's docstring for why this runs here and not in the view's
    request. Restore itself has no hard time limit of its own (big stores
    legitimately take minutes); this task's own ``time_limit``/
    ``soft_time_limit`` are set well above the global 120s/90s default
    (CELERY_TASK_TIME_LIMIT/SOFT) for exactly that reason -- the global
    default is fine for every other task in this app, just not this one."""
    from .models import StoreRestoreJob
    from .store_backup import BackupError, restore_store

    try:
        job = StoreRestoreJob.objects.select_related("project", "requested_by").get(pk=job_id)
    except StoreRestoreJob.DoesNotExist:
        logger.warning("restore job %s vanished before it could run", job_id)
        return

    job.status = StoreRestoreJob.Status.RUNNING
    job.save(update_fields=["status", "updated_at"])

    try:
        archive_bytes = job.archive.read()
    except Exception as exc:  # noqa: BLE001
        logger.exception("restore job %s: could not read uploaded archive", job_id)
        job.status = StoreRestoreJob.Status.ERROR
        job.error = f"Could not read the uploaded archive: {exc}"
        job.save(update_fields=["status", "error", "updated_at"])
        return

    try:
        counts = restore_store(
            job.project, archive_bytes, actor=job.requested_by,
            include_sensitive=job.include_sensitive,
        )
    except BackupError as exc:
        job.status = StoreRestoreJob.Status.ERROR
        job.error = str(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("restore job %s: restore_store failed", job_id)
        job.status = StoreRestoreJob.Status.ERROR
        job.error = f"Restore failed: {exc}"
    else:
        job.status = StoreRestoreJob.Status.DONE
        job.counts = counts

    # The archive was only ever needed to get the bytes here -- don't keep a
    # second multi-hundred-MB copy of every restore around forever.
    job.archive.delete(save=False)
    job.save(update_fields=["status", "counts", "error", "archive", "updated_at"])


@shared_task(name="apps.control.tasks.purge_stale_restore_uploads_task")
def purge_stale_restore_uploads_task():
    """Clean up abandoned chunked-upload .part files (see
    chunked_upload.purge_stale's docstring) -- a closed tab or crashed
    browser mid-upload never calls finalize() or abort(), so these would
    otherwise sit on disk forever."""
    from .chunked_upload import purge_stale

    removed = purge_stale(max_age_seconds=24 * 3600)
    if removed:
        logger.info("restore uploads: purged %s stale .part file(s)", removed)
    return {"removed": removed}


def queue_store_restore(project, archive: bytes, *, actor=None,
                        include_sensitive: bool = False, source_label: str = ""):
    """Save an uploaded backup .zip onto a new :class:`StoreRestoreJob` and
    hand it to :func:`run_store_restore_task`. Called from the owner,
    platform-admin and snapshot restore views in place of calling
    ``store_backup.restore_store`` directly -- see that job model's
    docstring for why."""
    from django.core.files.base import ContentFile

    from .models import StoreRestoreJob

    job = StoreRestoreJob.objects.create(
        project=project, include_sensitive=include_sensitive,
        source_label=source_label, requested_by=actor,
    )
    slug = project.slug or f"store-{project.pk}"
    job.archive.save(f"{slug}-restore-{job.pk}.zip", ContentFile(archive), save=True)
    run_store_restore_task.delay(job.pk)
    return job
