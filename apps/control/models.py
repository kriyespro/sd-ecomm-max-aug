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

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.project.name} backup {self.created_at:%Y-%m-%d}"
