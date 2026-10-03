"""Internal CRM + daily reporting for the platform team (super admin + DGCs).

Not tenant-scoped: this is the platform's own sales / onboarding workflow —
leads we cold-call, demos we give, DGCs we train, money we collect, and the
store set-up work we hand to DGCs.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel

User = settings.AUTH_USER_MODEL


class LeadStatus(models.TextChoices):
    NEW = "new", "New"
    CONTACTED = "contacted", "Contacted"
    INTERESTED = "interested", "Interested"
    DEMO_BOOKED = "demo_booked", "Demo booked"
    DEMO_DONE = "demo_done", "Demo done"
    NEGOTIATING = "negotiating", "Negotiating"
    WON = "won", "Won"
    LOST = "lost", "Lost"


OPEN_LEAD_STATUSES = [
    LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.INTERESTED,
    LeadStatus.DEMO_BOOKED, LeadStatus.DEMO_DONE, LeadStatus.NEGOTIATING,
]


class Lead(TimeStampedModel):
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True, db_index=True)
    business = models.CharField(max_length=160, blank=True)
    city = models.CharField(max_length=80, blank=True)
    source = models.CharField(max_length=60, blank=True, help_text="Cold call, referral, ad…")
    status = models.CharField(max_length=14, choices=LeadStatus.choices,
                              default=LeadStatus.NEW, db_index=True)
    notes = models.TextField(blank=True)

    assigned_to = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="crm_leads")
    assigned_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    next_follow_up = models.DateField(null=True, blank=True, db_index=True)
    # Archive = hide from every working view (list, board, queue, counts) but
    # keep the row and its history. Restorable; nothing here deletes a lead.
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    # When the lead entered its current stage — drives "3d in stage" on the board.
    stage_changed_at = models.DateTimeField(null=True, blank=True)
    converted_project = models.ForeignKey("projects.Project", null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._orig_status = self.__dict__.get("status")

    def refresh_from_db(self, *args, **kwargs):
        super().refresh_from_db(*args, **kwargs)
        self._orig_status = self.__dict__.get("status")

    def save(self, *args, **kwargs):
        if self.pk is None or self.status != self._orig_status:
            self.stage_changed_at = timezone.now()
            uf = kwargs.get("update_fields")
            if uf is not None:
                kwargs["update_fields"] = set(uf) | {"stage_changed_at"}
        super().save(*args, **kwargs)
        self._orig_status = self.status

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    @property
    def is_open(self):
        return self.status in OPEN_LEAD_STATUSES

    @property
    def wa_number(self):
        """Digits-only number for a wa.me link; bare 10-digit numbers get +91."""
        digits = "".join(c for c in self.phone if c.isdigit())
        return f"91{digits}" if len(digits) == 10 else digits


class ActivityKind(models.TextChoices):
    CALL = "call", "Call"
    WHATSAPP = "whatsapp", "WhatsApp"
    DEMO = "demo", "Demo given"
    FOLLOW_UP = "follow_up", "Follow-up"
    STORE_SETUP = "store_setup", "Store set-up"
    PRODUCT_ENTRY = "product_entry", "Product entry"
    MAINTENANCE = "maintenance", "Store maintenance"
    STAGE = "stage_change", "Stage change"


class ActivityOutcome(models.TextChoices):
    CONNECTED = "connected", "Connected"
    NOT_REACHABLE = "not_reachable", "Not reachable"
    NO_ANSWER = "no_answer", "No answer"
    CALL_BACK = "call_back", "Asked to call back"
    NOT_INTERESTED = "not_interested", "Not interested"
    DONE = "done", "Done"


class Activity(TimeStampedModel):
    """One unit of work. Everything on the reporting board counts rows here (or
    in Collection / TrainingLog)."""

    actor = models.ForeignKey(User, on_delete=models.CASCADE, related_name="crm_activities")
    kind = models.CharField(max_length=14, choices=ActivityKind.choices, db_index=True)
    outcome = models.CharField(max_length=14, choices=ActivityOutcome.choices, blank=True)
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="activities")
    project = models.ForeignKey("projects.Project", null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=300, blank=True)
    count = models.PositiveIntegerField(default=1, help_text="e.g. products entered in one go.")
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["actor", "kind", "occurred_at"])]

    def __str__(self):
        return f"{self.actor} · {self.get_kind_display()}"


class CollectionMode(models.TextChoices):
    SUBSCRIPTION = "subscription", "Subscription invoice"
    CASH = "cash", "Cash"
    UPI = "upi", "UPI"
    BANK = "bank", "Bank transfer"


class CollectionStatus(models.TextChoices):
    UNVERIFIED = "unverified", "Unverified"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"


class Collection(TimeStampedModel):
    """Money brought in. Subscription invoices land here automatically (already
    verified); offline cash/UPI is entered by the DGC and counts on the board
    only once a platform admin verifies it."""

    collected_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="crm_collections")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    mode = models.CharField(max_length=14, choices=CollectionMode.choices)
    reference = models.CharField(max_length=120, blank=True, help_text="UTR / receipt no.")
    proof = models.ImageField(upload_to="crm/proof/", blank=True, null=True)
    project = models.ForeignKey("projects.Project", null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="+")
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="collections")
    invoice = models.OneToOneField("billing.Invoice", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="crm_collection")
    note = models.CharField(max_length=300, blank=True)
    collected_on = models.DateField(default=timezone.localdate, db_index=True)

    status = models.CharField(max_length=10, choices=CollectionStatus.choices,
                              default=CollectionStatus.UNVERIFIED, db_index=True)
    verified_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-collected_on", "-created_at"]

    def __str__(self):
        return f"₹{self.amount} · {self.collected_by} ({self.status})"

    def save(self, *args, **kwargs):
        from apps.media.services import shrink_image_field

        shrink_image_field(self.proof, target_kb=150, max_edge=1600)
        super().save(*args, **kwargs)


class TrainingStatus(models.TextChoices):
    PENDING = "pending", "Awaiting trainer"
    CONFIRMED = "confirmed", "Confirmed"
    REJECTED = "rejected", "Rejected"


class TrainingLog(TimeStampedModel):
    """A DGC was trained by another DGC. The trainee logs it and names the
    trainer; it counts once the trainer (or a platform admin) confirms."""

    trainee = models.ForeignKey(User, on_delete=models.CASCADE, related_name="crm_trainings")
    trainer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="crm_trained")
    topic = models.CharField(max_length=160, blank=True)
    trained_on = models.DateField(default=timezone.localdate, db_index=True)
    status = models.CharField(max_length=10, choices=TrainingStatus.choices,
                              default=TrainingStatus.PENDING, db_index=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-trained_on", "-created_at"]

    def __str__(self):
        return f"{self.trainee} trained by {self.trainer}"


class TaskStatus(models.TextChoices):
    OPEN = "open", "Open"
    DONE = "done", "Done"
    CANCELLED = "cancelled", "Cancelled"


class Task(TimeStampedModel):
    """A to-do the super admin hands to someone."""

    title = models.CharField(max_length=200)
    detail = models.TextField(blank=True)
    assignee = models.ForeignKey(User, on_delete=models.CASCADE, related_name="crm_tasks")
    assigned_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="tasks")
    project = models.ForeignKey("projects.Project", null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="+")
    due_on = models.DateField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=10, choices=TaskStatus.choices,
                              default=TaskStatus.OPEN, db_index=True)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "due_on", "-created_at"]

    def __str__(self):
        return self.title


class TargetMetric(models.TextChoices):
    CALLS = "calls", "Calls / day"
    DEMOS = "demos", "Demos / day"
    COLLECTION = "collection", "Collection ₹ / day"


class DailyTarget(TimeStampedModel):
    """Per-person daily goal. ``user`` null = the default for everyone."""

    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE,
                             related_name="crm_targets")
    metric = models.CharField(max_length=12, choices=TargetMetric.choices)
    value = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "metric"], name="crm_target_user_metric")]


class WorkKind(models.TextChoices):
    CATALOG = "catalog", "Product entry"
    MAINTENANCE = "maintenance", "Store maintenance"


class WorkRequestStatus(models.TextChoices):
    OPEN = "open", "Open"
    ASSIGNED = "assigned", "Assigned"
    DONE = "done", "Done"
    CANCELLED = "cancelled", "Cancelled"


class StoreWorkRequest(TimeStampedModel):
    """"This store needs product entry / maintenance." Raised by the store
    owner or a DGC; only a platform admin assigns it (no self-claiming)."""

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE,
                                related_name="work_requests")
    kind = models.CharField(max_length=12, choices=WorkKind.choices, default=WorkKind.CATALOG)
    note = models.TextField(blank=True)
    requested_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    status = models.CharField(max_length=10, choices=WorkRequestStatus.choices,
                              default=WorkRequestStatus.OPEN, db_index=True)
    assigned_to = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="crm_work")
    assigned_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    assigned_at = models.DateTimeField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "-created_at"]

    def __str__(self):
        return f"{self.project} · {self.get_kind_display()}"


class StoreAssignment(TimeStampedModel):
    """Grants a DGC working access to one store's catalogue / content without
    making them its commission manager (that stays Subscription.manager).
    Orders, customers, money, billing and team stay blocked — see
    apps.projects.services.projects_for_user + dgc_without_membership."""

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE,
                                related_name="work_assignments")
    assignee = models.ForeignKey(User, on_delete=models.CASCADE, related_name="crm_assignments")
    scope = models.CharField(max_length=12, choices=WorkKind.choices, default=WorkKind.CATALOG)
    request = models.ForeignKey(StoreWorkRequest, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="assignments")
    assigned_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    is_active = models.BooleanField(default=True, db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(
            fields=["project", "assignee", "scope"], condition=models.Q(is_active=True),
            name="crm_one_active_assignment")]

    def __str__(self):
        return f"{self.assignee} → {self.project} ({self.scope})"
