"""Analytics roll-ups (project.md section 21).

``DailyMetric`` is a per-day snapshot recomputed from orders when order events
fire. ``EventCounter`` is a generic per-day tally for lightweight funnel counts
(product views, add-to-cart, etc.) the storefront can POST.
"""

from decimal import Decimal

from django.db import models

from apps.core.models import TenantScopedModel


class DailyMetric(TenantScopedModel):
    date = models.DateField(db_index=True)
    orders_count = models.PositiveIntegerField(default=0)
    revenue = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    items_sold = models.PositiveIntegerField(default=0)
    new_customers = models.PositiveIntegerField(default=0)
    returning_customers = models.PositiveIntegerField(default=0)
    cancelled_count = models.PositiveIntegerField(default=0)
    refunded_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    aov = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(fields=["project", "date"], name="uniq_dailymetric"),
        ]

    def __str__(self):
        return f"{self.project_id} {self.date}"


class EventCounter(TenantScopedModel):
    date = models.DateField(db_index=True)
    key = models.CharField(max_length=60)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-date", "key"]
        constraints = [
            models.UniqueConstraint(fields=["project", "date", "key"], name="uniq_eventcounter"),
        ]

    def __str__(self):
        return f"{self.key} {self.date}: {self.count}"
