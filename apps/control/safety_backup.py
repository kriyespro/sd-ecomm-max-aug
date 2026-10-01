"""Automatic safety backup taken right before a destructive demo-content action.

Same engine and format as the owner's manual / daily backups
(``store_backup.dump_store``), stored as a labelled ``StoreBackupSnapshot`` so it
can be restored from the page that triggered it. Catalogue + CMS + theme only —
orders and customers are never touched by those actions, so they are not
captured (and a restore never wipes them).
"""

from django.core.files.base import ContentFile
from django.utils import timezone

from . import store_backup
from .models import StoreBackupSnapshot

KEEP = 3  # newest safety backups kept per store


def take(project, label):
    """Back ``project`` up now. Returns the snapshot. Raises ``BackupError`` /
    any storage error — callers must abort the destructive action on failure."""
    blob = store_backup.dump_store(project, include_sensitive=False)
    snap = StoreBackupSnapshot(project=project, size_bytes=len(blob), label=label,
                               includes_orders=False)
    slug = project.slug or f"store-{project.pk}"
    snap.archive.save(f"{slug}-safety-{timezone.now():%Y%m%d-%H%M%S}.zip",
                      ContentFile(blob), save=True)
    stale = (StoreBackupSnapshot.objects.filter(project=project, includes_orders=False)
             .exclude(label="").order_by("-created_at")[KEEP:])
    for old in stale:
        old.archive.delete(save=False)
        old.delete()
    return snap


def latest(project):
    return (StoreBackupSnapshot.objects.filter(project=project, includes_orders=False)
            .exclude(label="").order_by("-created_at").first())
