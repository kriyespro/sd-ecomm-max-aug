"""Per-store social auto-share.

When a store owner connects Pinterest and/or a Google Business Profile once,
every product they publish is posted to those channels automatically (a Pin /
a local post). Referral traffic + Pinterest & Google indexing — a modest but
real SEO signal.

v1: per-seller own OAuth app. The seller registers a Pinterest / Google app,
points its redirect URI at our callback, pastes the client id + secret, then
clicks Connect. We hold the tokens and refresh them.
"""

from django.core.cache import cache
from django.db import models

from apps.core.models import TenantScopedModel


class SocialProvider(models.TextChoices):
    PINTEREST = "pinterest", "Pinterest"
    GBP = "gbp", "Google Business Profile"


IMPLEMENTED = ("pinterest", "gbp")

_CACHE_KEY = "social:accounts:{project_id}"
_CACHE_TTL = 300


class SocialAccount(TenantScopedModel):
    provider = models.CharField(max_length=16, choices=SocialProvider.choices)

    client_id = models.CharField(max_length=255, blank=True)
    client_secret = models.CharField(max_length=255, blank=True)

    access_token = models.TextField(blank=True)
    refresh_token = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    scope = models.CharField(max_length=255, blank=True)

    # Where posts land. Pinterest: a board id. GBP: a location resource name
    # (``accounts/123/locations/456``).
    target_id = models.CharField(max_length=255, blank=True)
    target_name = models.CharField(max_length=255, blank=True)
    account_name = models.CharField(max_length=255, blank=True)

    is_active = models.BooleanField(default=False)
    auto_share = models.BooleanField(default=True)
    last_error = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "provider"],
                                    name="uniq_social_provider_per_project"),
        ]
        ordering = ["provider"]

    def __str__(self):
        return f"{self.get_provider_display()} · {self.project_id}"

    @property
    def ready(self):
        return bool(self.is_active and self.access_token and self.target_id)

    @property
    def connected(self):
        return bool(self.access_token and self.refresh_token)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.delete(_CACHE_KEY.format(project_id=self.project_id))

    def delete(self, *args, **kwargs):
        pid = self.project_id
        super().delete(*args, **kwargs)
        cache.delete(_CACHE_KEY.format(project_id=pid))


def accounts_for(project):
    """Cached list of a project's ``SocialAccount`` rows."""
    if project is None:
        return []
    key = _CACHE_KEY.format(project_id=project.pk)
    cached = cache.get(key)
    if cached is not None:
        return cached
    rows = list(SocialAccount.objects.filter(project=project))
    cache.set(key, rows, _CACHE_TTL)
    return rows


def auto_share_targets(project):
    return [a for a in accounts_for(project) if a.ready and a.auto_share]


class ShareStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    POSTED = "posted", "Posted"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


class SocialPost(TenantScopedModel):
    provider = models.CharField(max_length=16, choices=SocialProvider.choices)
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE,
                                related_name="social_posts")
    external_id = models.CharField(max_length=128, blank=True)
    url = models.URLField(blank=True)
    status = models.CharField(max_length=12, choices=ShareStatus.choices,
                              default=ShareStatus.QUEUED, db_index=True)
    error = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["project", "provider", "product"],
                                    name="uniq_social_post_per_product_provider"),
        ]

    def __str__(self):
        return f"{self.provider} {self.product_id} ({self.status})"
