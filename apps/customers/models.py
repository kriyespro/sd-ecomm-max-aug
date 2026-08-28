"""Customer records (project.md section 13).

A ``Customer`` is store-scoped: the same person is a separate Customer row per
project they buy from. It may be linked to an auth user or be a pure guest
(email only). Order totals are denormalised onto the row and kept fresh by
``services.sync_customer_stats`` (wired via a signal on Order).
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TenantScopedModel, TimeStampedModel


class Segment(models.TextChoices):
    NEW = "new", "New"
    RETURNING = "returning", "Returning"
    VIP = "vip", "VIP"
    HIGH_VALUE = "high_value", "High value"
    INACTIVE = "inactive", "Inactive"


class CustomerGroup(TenantScopedModel):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=90, blank=True)
    description = models.CharField(max_length=255, blank=True)
    discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Automatic % off for members (0 = none).",
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["project", "slug"], name="uniq_customergroup_slug"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.name)[:90]
        super().save(*args, **kwargs)


class Customer(TenantScopedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="customer_records",
    )
    email = models.EmailField(db_index=True)
    first_name = models.CharField(max_length=120, blank=True)
    last_name = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=32, blank=True)

    group = models.ForeignKey(
        CustomerGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="customers",
    )
    tags = models.JSONField(default=list, blank=True)
    segment = models.CharField(max_length=20, choices=Segment.choices, default=Segment.NEW)

    is_active = models.BooleanField(default=True)
    is_blocked = models.BooleanField(default=False)
    marketing_opt_in = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    orders_count = models.PositiveIntegerField(default=0)
    total_spent = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    last_order_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["project", "email"], name="uniq_customer_email_per_project"),
        ]
        indexes = [
            models.Index(fields=["project", "segment"]),
        ]

    def __str__(self):
        return self.full_name or self.email

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()


class CustomerAddress(TimeStampedModel):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField(max_length=40, blank=True)
    name = models.CharField(max_length=160, blank=True)
    line1 = models.CharField(max_length=255)
    line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=120)
    state = models.CharField(max_length=120, blank=True)
    postal_code = models.CharField(max_length=20)
    country = models.CharField(max_length=2, default="IN")
    phone = models.CharField(max_length=32, blank=True)
    is_default_shipping = models.BooleanField(default=False)
    is_default_billing = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_default_shipping", "-is_default_billing", "id"]

    def __str__(self):
        return f"{self.label or self.city} <{self.customer_id}>"

    def as_dict(self):
        return {
            "name": self.name, "line1": self.line1, "line2": self.line2,
            "city": self.city, "state": self.state, "postal_code": self.postal_code,
            "country": self.country, "phone": self.phone,
        }
