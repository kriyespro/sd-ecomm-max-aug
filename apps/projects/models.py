"""The Project (Store / tenant) model and its domain mappings.

A Project owns all business data in every other app via a ``project`` FK
(project.md section 4). Operating many stores means adding rows here, never a
new Django install.
"""

import re

from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator
from django.db import models
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.text import slugify

from apps.core.models import TimeStampedModel

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$"
)


def validate_hostname(value):
    host = (value or "").strip().lower().rstrip(".")
    if not _HOSTNAME_RE.match(host):
        raise ValidationError("Enter a valid domain name, e.g. shop.example.com")


class Project(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=120, validators=[MinLengthValidator(2)])
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )

    # Identity / branding
    logo = models.ImageField(upload_to="projects/logos/", blank=True, null=True)
    favicon = models.ImageField(upload_to="projects/favicons/", blank=True, null=True)

    # Primary domain. Additional / custom domains live in Domain.
    primary_domain = models.CharField(
        max_length=253, unique=True, null=True, blank=True, db_index=True
    )

    # Opted in to sell wholesale/dropship to other stores on the platform
    # (apps.b2b). Owner-only toggle; reversible, independent of ``status``.
    is_b2b_seller = models.BooleanField(default=False)

    # Storefront skins this store is allowed to use. Empty = every active skin
    # is allowed. Managed by a platform manager; the store owner picks one of
    # these on the Theme screen.
    allowed_skins = models.ManyToManyField(
        "cms.Skin", blank=True, related_name="projects"
    )

    # Locale / commerce basics
    timezone = models.CharField(max_length=64, default="UTC")
    currency = models.CharField(max_length=3, default="INR")
    country = models.CharField(max_length=2, default="IN")
    state = models.CharField(max_length=64, blank=True)

    # Grouped configuration. Kept as JSON so a store can be reconfigured without
    # a migration; validated by forms/serializers at the edge.
    tax_config = models.JSONField(default=dict, blank=True)
    branding = models.JSONField(default=dict, blank=True)
    email_config = models.JSONField(default=dict, blank=True)
    payment_config = models.JSONField(default=dict, blank=True)
    shipping_config = models.JSONField(default=dict, blank=True)
    seo_config = models.JSONField(default=dict, blank=True)
    notification_config = models.JSONField(default=dict, blank=True)
    feature_flags = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:130] or "store"
            slug = base
            i = 2
            while Project.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                slug = f"{base}-{i}"
                i += 1
            self.slug = slug
        if self.primary_domain:
            self.primary_domain = self.primary_domain.lower().strip()
        super().save(*args, **kwargs)

    @property
    def is_live(self):
        return self.status == self.Status.ACTIVE

    def feature_enabled(self, key, default=False):
        return bool(self.feature_flags.get(key, default))


class Domain(TimeStampedModel):
    """A hostname routed to a project (project.md section 26, multi-domain).

    A domain only starts routing traffic once ``is_verified`` is set — proven by
    a DNS TXT record (see ``apps.projects.domains``).
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="domains"
    )
    host = models.CharField(max_length=253, unique=True, db_index=True,
                            validators=[validate_hostname])
    is_primary = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)
    verification_token = models.CharField(max_length=48, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_check_error = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["host"]
        constraints = [
            models.UniqueConstraint(
                fields=["project"], condition=models.Q(is_primary=True),
                name="uniq_primary_domain_per_project",
            ),
        ]

    def __str__(self):
        return self.host

    @property
    def txt_name(self):
        return f"_sd-verify.{self.host}"

    @property
    def txt_value(self):
        return f"sd-verify={self.verification_token}"

    def save(self, *args, **kwargs):
        self.host = (self.host or "").strip().lower().rstrip(".")
        if not self.verification_token:
            self.verification_token = get_random_string(32)
        if self.is_verified and self.verified_at is None:
            self.verified_at = timezone.now()
        super().save(*args, **kwargs)
