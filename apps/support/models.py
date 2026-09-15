"""Feature requests + support tickets raised from Mission Control.

Two visibility shapes share one model:

* ``FEATURE_REQUEST`` tickets are a platform-wide backlog — any owner,
  manager or DGC can read, comment and vote on one regardless of which store
  filed it (dedupe + prioritise instead of everyone filing the same idea).
* ``BUG`` / ``SUPPORT`` / ``BILLING`` tickets are private to the filing
  store's own team (owner/manager/DGC) plus the platform admin who triages
  the queue at ``/admin/tickets/``.

``project`` is always set (every Mission Control action happens inside an
active store) even for a feature request — it's kept for "requested by"
attribution, not as a visibility filter.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel, TimeStampedModel


class TicketKind(models.TextChoices):
    FEATURE_REQUEST = "feature_request", "Feature request"
    BUG = "bug", "Bug"
    SUPPORT = "support", "Support"
    BILLING = "billing", "Billing"


class TicketStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_PROGRESS = "in_progress", "In progress"
    PLANNED = "planned", "Planned"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


CLOSED_STATUSES = {TicketStatus.RESOLVED, TicketStatus.CLOSED}


class TicketPriority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


class ReporterRole(models.TextChoices):
    OWNER = "owner", "Owner"
    MANAGER = "manager", "Manager"
    DGC = "dgc", "DGC"
    ADMIN = "admin", "Platform admin"


def reporter_role_for(user, project) -> str:
    """Snapshot of the viewer's role at the moment they act — kept on the row
    even if their real role changes later."""
    from apps.accounts.models import StoreRole
    from apps.accounts.permissions import is_platform_admin, is_platform_staff, store_role

    if is_platform_admin(user):
        return ReporterRole.ADMIN
    role = store_role(user, project)
    if role == StoreRole.OWNER:
        return ReporterRole.OWNER
    if role == StoreRole.MANAGER:
        return ReporterRole.MANAGER
    if is_platform_staff(user):
        return ReporterRole.DGC
    return ReporterRole.MANAGER  # StoreRoleRequiredMixin already excludes staff/anon


class Ticket(TenantScopedModel):
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="support_tickets",
    )
    created_by_role = models.CharField(max_length=10, choices=ReporterRole.choices)

    kind = models.CharField(max_length=20, choices=TicketKind.choices)
    subject = models.CharField(max_length=200)
    description = models.TextField()

    status = models.CharField(max_length=12, choices=TicketStatus.choices,
                              default=TicketStatus.OPEN, db_index=True)
    priority = models.CharField(max_length=8, choices=TicketPriority.choices,
                                default=TicketPriority.NORMAL)

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="assigned_tickets",
        limit_choices_to={"is_staff": True},
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["kind", "status"]),
            models.Index(fields=["project", "kind"]),
        ]

    def __str__(self):
        return f"[{self.get_kind_display()}] {self.subject}"

    @property
    def is_global(self) -> bool:
        """Feature requests are the platform-wide board; everything else is
        private to the filing store."""
        return self.kind == TicketKind.FEATURE_REQUEST

    @property
    def is_closed(self) -> bool:
        return self.status in CLOSED_STATUSES


class TicketMessage(TimeStampedModel):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="support_messages",
    )
    author_role = models.CharField(max_length=10, choices=ReporterRole.choices)
    body = models.TextField()
    # Admin-only triage note — never rendered on the reporter-facing thread.
    is_internal_note = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Message on #{self.ticket_id} by {self.author_id}"


class TicketVote(TimeStampedModel):
    """One vote per user per feature request — dedupe signal + rough sort key."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="support_ticket_votes")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["ticket", "user"], name="uniq_ticketvote_per_user"),
        ]

    def __str__(self):
        return f"Vote on #{self.ticket_id} by {self.user_id}"
