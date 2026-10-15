from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel

RETENTION_DAYS = 7


class StoreBackupSnapshot(TimeStampedModel):
    """One store's automatic daily backup — a full dump_store(project,
    include_sensitive=True) archive (catalog/CMS/theme + orders/customers/
    payments), same engine and format as the owner's own manual backup
    (apps.control.store_backup, apps.control.backup_views). Created by
    apps.control.tasks.daily_store_backup_task; pruned to the last
    RETENTION_DAYS days by the same task. Owner-only data -- never shown to
    a DGC without a real store membership (see backup_views._include_sensitive)."""

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="backup_snapshots",
    )
    archive = models.FileField(upload_to="backups/%Y/%m/%d/")
    size_bytes = models.PositiveBigIntegerField(default=0)
    counts = models.JSONField(default=dict, blank=True)
    # Safety snapshots taken automatically just before a destructive action
    # (demo reset / remove) carry a label, hold catalogue + CMS + theme only
    # (includes_orders=False -- orders/customers are never wiped by those
    # actions) and are exempt from the daily 7-day prune.
    label = models.CharField(max_length=80, blank=True)
    includes_orders = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.project.name} backup {self.created_at:%Y-%m-%d}"


class StoreRestoreJob(TimeStampedModel):
    """One queued/running/finished store_backup.restore_store() run.

    Restoring a big store is one INSERT + media write per row (no
    bulk_create -- fks get remapped row by row) -- easily minutes for a
    few thousand products with images. Doing that inline in the request
    that uploads the backup .zip hits Cloudflare's ~100s edge timeout
    (524) long before Django responds, even though the restore itself
    would've succeeded. So the view only creates this row (status=PENDING,
    the uploaded archive saved to ``archive``) and queues
    apps.control.tasks.run_store_restore_task(job.pk) -- the actual wipe +
    rebuild happens in a Celery worker, which has its own much longer
    time limit (see that task's ``time_limit=``), not the one HTTP
    request/response cycle. ``archive`` is deleted once the job finishes
    (done or error) -- it's only needed to get the bytes to the worker."""

    class Status(models.TextChoices):
        PENDING = "pending", "Queued"
        RUNNING = "running", "Restoring"
        DONE = "done", "Done"
        ERROR = "error", "Failed"

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="restore_jobs",
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    include_sensitive = models.BooleanField(default=False)
    archive = models.FileField(upload_to="restore_tmp/%Y/%m/%d/", blank=True)
    source_label = models.CharField(max_length=120, blank=True)
    counts = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.project.name} restore ({self.status})"
