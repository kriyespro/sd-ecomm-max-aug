"""Daily "coach": a platform-curated checklist that nudges store owners to do
the few things that grow a store, plus a message/tip channel for the superadmin.

``Mission`` rows are platform-wide (not tenant-scoped) and edited only by a
platform admin. ``MissionLog`` is the per-store, per-day record — it is what
makes streaks and "last activity" possible.
"""

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class MissionKind(models.TextChoices):
    TASK = "task", "Checklist task"
    TIP = "tip", "Tip (did you know?)"
    MESSAGE = "message", "Message from the platform"


class Mission(TimeStampedModel):
    kind = models.CharField(max_length=10, choices=MissionKind.choices, default=MissionKind.TASK)
    title = models.CharField(max_length=160)
    detail = models.TextField(
        blank=True, help_text="Longer text — the tip itself, or why this task matters."
    )
    icon = models.CharField(max_length=8, blank=True, default="✅", help_text="One emoji.")
    auto_key = models.CharField(
        max_length=40, blank=True,
        help_text="Tick itself when the store does the thing. Blank = the owner ticks it.",
    )
    target = models.PositiveIntegerField(
        default=1, help_text="How many (e.g. 5 products). Only used by auto tasks."
    )
    points = models.PositiveIntegerField(default=10)
    cta_label = models.CharField(max_length=40, blank=True, help_text="Button text, e.g. “Add products”.")
    cta_url = models.CharField(
        max_length=300, blank=True,
        help_text="Page to open: /admin/… path or full link. {store_url} = the store's own site.",
    )
    sort_order = models.PositiveIntegerField(default=0)
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True, help_text="Last day it shows. Blank = no end.")
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ["kind", "sort_order", "id"]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.title}"

    @property
    def is_auto(self):
        return bool(self.auto_key)


class MissionLog(TimeStampedModel):
    """One row per store × mission × day. For auto missions ``progress`` is the
    last counted value; ``completed_at`` is set once it reaches the target (and
    stays set — doing the work, then deleting a product, doesn't un-do the day)."""

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="mission_logs"
    )
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="logs")
    day = models.DateField(db_index=True)
    progress = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-day", "mission_id"]
        constraints = [
            models.UniqueConstraint(fields=["project", "mission", "day"], name="uniq_missionlog_day"),
        ]
        indexes = [models.Index(fields=["project", "day"])]
