"""Shared abstract models and the platform-wide audit log.

Every business model in other apps inherits from :class:`TenantScopedModel` so
that all rows carry a ``project`` FK (see project.md sections 4 and 29). Queries
must always be filtered by the resolved project — never return data across
projects.
"""

from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class SeoFieldsModel(models.Model):
    """Reusable meta fields for anything the storefront renders a page for."""

    seo_title = models.CharField(max_length=180, blank=True)
    seo_description = models.CharField(max_length=320, blank=True)
    seo_keywords = models.CharField(max_length=255, blank=True)

    class Meta:
        abstract = True


class TenantScopedModel(TimeStampedModel):
    """Base for all store-owned business data."""

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="%(class)ss",
    )

    class Meta:
        abstract = True


class AuditLog(TimeStampedModel):
    """Who changed what, when, in which project (project.md section 17)."""

    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        LOGIN = "login", "Login"
        IMPERSONATE = "impersonate", "Impersonate"
        OTHER = "other", "Other"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    target_type = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "-created_at"]),
            models.Index(fields=["target_type", "target_id"]),
        ]

    def __str__(self):
        who = self.actor or "system"
        return f"{who} {self.action} {self.target_type}#{self.target_id}"


_TESTIMONIALS_CACHE_KEY = "marketing:testimonials:v1"
_TESTIMONIALS_CACHE_TTL = 300


class Testimonial(TimeStampedModel):
    """A real seller quote shown on the public marketing/ad landing pages.

    Platform-admin managed (not tenant-scoped). A quote only goes live when it
    is both published AND ``consent_confirmed`` -- i.e. the seller agreed to
    be quoted -- so a half-entered row can never reach a paid-ad page.
    """

    quote = models.TextField(max_length=500)
    name = models.CharField(max_length=80)
    role = models.CharField(
        max_length=120, blank=True,
        help_text="e.g. “Owner, Nisha Boutique, Pune”.",
    )
    pages = models.JSONField(
        default=list, blank=True,
        help_text="Landing-page slugs this quote appears on. Leave empty for all pages.",
    )
    consent_confirmed = models.BooleanField(
        default=False,
        help_text="I have this seller's permission to publish their name and words.",
    )
    is_published = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "-created_at"]

    def __str__(self):
        return f"{self.name}: {self.quote[:40]}"

    def save(self, *args, **kwargs):
        from django.core.cache import cache

        super().save(*args, **kwargs)
        cache.delete(_TESTIMONIALS_CACHE_KEY)

    def delete(self, *args, **kwargs):
        from django.core.cache import cache

        out = super().delete(*args, **kwargs)
        cache.delete(_TESTIMONIALS_CACHE_KEY)
        return out


def testimonials_for(slug):
    """Live quotes for a landing page as ``[(quote, name, role), ...]``."""
    from django.core.cache import cache

    rows = cache.get(_TESTIMONIALS_CACHE_KEY)
    if rows is None:
        rows = list(
            Testimonial.objects.filter(is_published=True, consent_confirmed=True)
            .values_list("quote", "name", "role", "pages")
        )
        cache.set(_TESTIMONIALS_CACHE_KEY, rows, _TESTIMONIALS_CACHE_TTL)
    return [(q, n, r) for q, n, r, pages in rows if not pages or slug in pages]
