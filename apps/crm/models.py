"""Internal CRM + daily reporting for the platform team (super admin + DGCs).

Not tenant-scoped: this is the platform's own sales / onboarding workflow —
leads we cold-call, demos we give, DGCs we train, money we collect, and the
store set-up work we hand to DGCs.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel

from .tz import biz_today

User = settings.AUTH_USER_MODEL


def normalize_phone(raw):
    """Digits only; for 10+ digits keep the last 10 so "+91 98765 43210",
    "098765-43210" and "9876543210" are the same person. Shorter strings are
    kept as digits (they can't be matched reliably but are still stored)."""
    digits = "".join(c for c in (raw or "") if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


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

# One fixed color per stage of the pipeline — the "win ladder" (new through
# to Won), a plain progression with no sorting or row-tinting attached. This
# is the documented source of truth; the Jinja macro (status_select in
# templates/control/crm/_crm.jinja) and the JS recolor-on-change handler
# (templates/control/crm/base_crm.jinja) each inline the identical map rather
# than importing it, so they have no context/JSON plumbing to keep working —
# a test asserts all three stay in step.
STATUS_COLOR = {
    LeadStatus.NEW: ("bg-slate-100", "text-slate-700"),
    LeadStatus.CONTACTED: ("bg-sky-50", "text-sky-800"),
    LeadStatus.INTERESTED: ("bg-amber-50", "text-amber-800"),
    LeadStatus.DEMO_BOOKED: ("bg-violet-50", "text-violet-800"),
    LeadStatus.DEMO_DONE: ("bg-indigo-50", "text-indigo-800"),
    LeadStatus.NEGOTIATING: ("bg-orange-100", "text-orange-800"),
    LeadStatus.WON: ("bg-emerald-50", "text-emerald-700"),
    LeadStatus.LOST: ("bg-rose-50", "text-rose-700"),
}


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
    # Normalised phone (see normalize_phone) — what duplicate checks compare.
    phone_norm = models.CharField(max_length=20, blank=True, db_index=True, editable=False)
    # Speed-to-lead: when it was handed to someone, and when they first reached out.
    assigned_at = models.DateTimeField(null=True, blank=True)
    first_touch_at = models.DateTimeField(null=True, blank=True)
    auto_assigned = models.BooleanField(default=False)
    # Where an inbound lead came from (utm_*, landing-page slug, ad ids…).
    acquisition = models.JSONField(default=dict, blank=True)
    # When a booked demo is due (set with "Demo booked").
    demo_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # When it entered its current stage — drives "3d in stage" on the board.
    stage_changed_at = models.DateTimeField(null=True, blank=True)
    converted_project = models.ForeignKey("projects.Project", null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._orig_status = self.__dict__.get("status")
        self._orig_assignee = self.__dict__.get("assigned_to_id")

    def refresh_from_db(self, *args, **kwargs):
        super().refresh_from_db(*args, **kwargs)
        self._orig_status = self.__dict__.get("status")
        self._orig_assignee = self.__dict__.get("assigned_to_id")

    def save(self, *args, **kwargs):
        if self.pk is None or self.status != self._orig_status:
            self.stage_changed_at = timezone.now()
            uf = kwargs.get("update_fields")
            if uf is not None:
                kwargs["update_fields"] = set(uf) | {"stage_changed_at"}
        # keep the normalised phone and assignment time in step with the row
        extra = set()
        norm = normalize_phone(self.phone)
        if norm != self.phone_norm:
            self.phone_norm = norm
            extra.add("phone_norm")
        if self.assigned_to_id and self.assigned_to_id != self._orig_assignee and not self.assigned_at:
            self.assigned_at = timezone.now()
            extra.add("assigned_at")
        elif self.assigned_to_id and self.assigned_to_id != self._orig_assignee and self._orig_assignee:
            self.assigned_at = timezone.now()      # re-assigned to someone else: clock restarts
            extra.add("assigned_at")
        uf = kwargs.get("update_fields")
        if uf is not None and extra:
            kwargs["update_fields"] = set(uf) | extra
        super().save(*args, **kwargs)
        self._orig_status = self.status
        self._orig_assignee = self.assigned_to_id

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
    collected_on = models.DateField(default=biz_today, db_index=True)

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
    REQUESTED = "requested", "Wants training"
    ASSIGNED = "assigned", "Trainer assigned"
    PENDING = "pending", "Awaiting confirmation"
    CONFIRMED = "confirmed", "Confirmed"
    REJECTED = "rejected", "Rejected"


class TrainingLog(TimeStampedModel):
    """One training: someone learns, someone teaches.

    Three ways in, one table:
    * trainee logs "I was trained by X"           -> pending (trainer confirms)
    * trainer adds a student they trained         -> pending (student confirms;
      a name-only student has no account, so only a platform admin can confirm)
    * someone asks for training                   -> requested; only a platform
      admin assigns a trainer (-> assigned); the trainer marks it trained
      (-> pending) and the other side confirms.
    It counts on the board only once confirmed.
    """

    trainee = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE,
                                related_name="crm_trainings")
    trainer = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE,
                                related_name="crm_trained")
    # A student who has no login yet (new recruit) — name + phone instead of a user.
    student_name = models.CharField(max_length=120, blank=True)
    student_phone = models.CharField(max_length=20, blank=True)
    topic = models.CharField(max_length=160, blank=True)
    trained_on = models.DateField(default=biz_today, db_index=True)
    status = models.CharField(max_length=10, choices=TrainingStatus.choices,
                              default=TrainingStatus.PENDING, db_index=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # Who created / last advanced it — the *other* side is the one who confirms.
    initiated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    assigned_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    assigned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-trained_on", "-created_at"]

    @property
    def student_label(self):
        if self.trainee_id:
            return self.trainee.get_full_name() or self.trainee.email or self.trainee.get_username()
        return self.student_name or "—"

    def __str__(self):
        return f"{self.student_label} / {self.trainer or 'no trainer yet'} ({self.status})"


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
    # Set on tasks the system creates (trial rescue, store health…) so a daily
    # job never creates the same task twice.
    auto_key = models.CharField(max_length=80, blank=True, db_index=True)
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


DEFAULT_STAGE_PROBABILITY = {
    "new": 3, "contacted": 8, "interested": 20,
    "demo_booked": 30, "demo_done": 45, "negotiating": 65,
}


class CrmSettings(TimeStampedModel):
    """Singleton (pk=1): the knobs a platform admin tunes for the whole CRM."""

    # -- routing / speed-to-lead
    auto_assign = models.BooleanField(default=True, help_text="Hand new inbound leads to a DGC instantly.")
    capacity_per_dgc = models.PositiveIntegerField(default=0, help_text="Max open leads per DGC for auto-assign. 0 = no limit.")
    alert_email = models.BooleanField(default=True, help_text="Email the DGC when a lead is assigned to them.")
    # -- automatic follow-ups
    trial_rescue_days = models.PositiveSmallIntegerField(default=3, help_text="Create a task this many days before a DGC's store trial ends. 0 = off.")
    health_no_products_days = models.PositiveSmallIntegerField(default=7, help_text="Task if a DGC's store still has no products after this many days. 0 = off.")
    health_no_orders_days = models.PositiveSmallIntegerField(default=14, help_text="Task if a live store has no orders after this many days. 0 = off.")
    recycle_days = models.PositiveSmallIntegerField(default=30, help_text="Return untouched open leads to the pool after this many days. 0 = off.")
    # -- admin alerts
    quiet_check = models.BooleanField(default=True, help_text="Email admins when a DGC has done nothing by midday.")
    quiet_after_hour = models.PositiveSmallIntegerField(default=12, help_text="Local hour (0-23) the quiet check runs after.")
    weekly_digest = models.BooleanField(default=True, help_text="Email a team summary every Monday.")
    digest_emails = models.TextField(blank=True, help_text="Comma-separated. Blank = every platform admin's email.")
    # -- the sales funnel every DGC is coached on (editable). Defaults follow
    #    "100 calls -> 10 meetings/demos -> 6 trials -> 3 paid clients" = 3%.
    conv_call_to_demo = models.PositiveSmallIntegerField(default=10, help_text="Of every 100 calls, how many become a meeting / demo (%).")
    conv_demo_to_trial = models.PositiveSmallIntegerField(default=60, help_text="Of the meetings / demos, how many start a free trial (%).")
    conv_trial_to_paid = models.PositiveSmallIntegerField(default=50, help_text="Of the trials, how many become paying clients with proper follow-up (%).")
    value_commission_pct = models.PositiveSmallIntegerField(default=20, help_text="The share of the YEARLY DGC price (not the public price) counted as a DGC's earning per client (%). A DGC can have their own on their person page. Estimates only.")
    min_ticket_override = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                              help_text="Smallest YEARLY DGC price used in the estimates. Blank = the cheapest plan's DGC yearly price.")
    working_days_month = models.PositiveSmallIntegerField(default=26, help_text="Working days per month used in the monthly projection.")
    # bookkeeping so the hourly jobs send each alert/digest once
    last_quiet_on = models.DateField(null=True, blank=True, editable=False)
    last_digest_on = models.DateField(null=True, blank=True, editable=False)
    # -- forecast
    avg_plan_price = models.DecimalField(max_digits=10, decimal_places=2, default=2999,
                                         help_text="Average monthly plan price used for the revenue forecast.")
    stage_probabilities = models.JSONField(default=dict, blank=True,
                                           help_text="Chance (%) a lead at each stage becomes a paying store.")

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def probability(self, stage):
        merged = {**DEFAULT_STAGE_PROBABILITY, **(self.stage_probabilities or {})}
        try:
            return max(0, min(100, int(merged.get(stage, 0))))
        except (TypeError, ValueError):
            return 0

    def __str__(self):
        return "CRM settings"


class CrmProfile(TimeStampedModel):
    """Per-DGC CRM preferences. A missing row means "defaults" (accepts leads)."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="crm_profile")
    accepts_leads = models.BooleanField(default=True, help_text="Off = skipped by auto-assign (e.g. on leave).")
    cities = models.CharField(max_length=200, blank=True,
                              help_text="Cities / areas this DGC covers, comma-separated. New leads from these go to them first.")
    # Per-DGC numbers set by a platform admin. Blank = use the team default in CrmSettings.
    conv_call_to_demo = models.PositiveSmallIntegerField(null=True, blank=True, help_text="% of this DGC's calls that become a meeting/demo.")
    conv_demo_to_trial = models.PositiveSmallIntegerField(null=True, blank=True, help_text="% of this DGC's meetings/demos that start a trial.")
    conv_trial_to_paid = models.PositiveSmallIntegerField(null=True, blank=True, help_text="% of this DGC's trials that become paying clients.")
    commission_pct = models.PositiveSmallIntegerField(null=True, blank=True, help_text="% of the DGC price counted as this DGC's earning per client.")
    # The DGC's own fee for what they do for a client (setup, product entry, training…).
    # Added on top of the commission in their "what a client is worth" numbers.
    service_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0,
                                         help_text="Your own service charge per client (₹), added to your earnings estimates.")

    def city_list(self):
        return [c.strip().lower() for c in self.cities.split(",") if c.strip()]

    def __str__(self):
        return f"CRM profile · {self.user}"


class TemplateKind(models.TextChoices):
    WHATSAPP = "whatsapp", "WhatsApp message"
    SCRIPT = "script", "Call script"


class MessageTemplate(TimeStampedModel):
    """Admin-managed snippets DGCs use with one tap.

    * whatsapp: opens WhatsApp with the text pre-filled for that lead.
    * script: a "what to say" card shown on the lead page for its stage.
    Placeholders: {name} {first_name} {business} {city} {dgc}.
    """

    kind = models.CharField(max_length=10, choices=TemplateKind.choices, default=TemplateKind.WHATSAPP)
    stage = models.CharField(max_length=14, blank=True, choices=LeadStatus.choices,
                             help_text="Scripts only: show for leads at this stage. Blank = every stage.")
    title = models.CharField(max_length=80)
    body = models.TextField(max_length=2000)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "order", "id"]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.title}"


class SourceSpend(TimeStampedModel):
    """Ad spend per lead source per month — lets the insights page show cost per
    lead / per won store."""

    source = models.CharField(max_length=60, db_index=True)
    month = models.DateField(help_text="Any date in the month.")
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["-month", "source"]
        constraints = [models.UniqueConstraint(fields=["source", "month"], name="crm_spend_source_month")]

    def save(self, *args, **kwargs):
        self.month = self.month.replace(day=1)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.source} {self.month:%b %Y}: ₹{self.amount}"
