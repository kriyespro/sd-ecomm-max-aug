"""Third-party marketing / conversion tracking per store.

One row per (store, provider). Only ``meta`` (Meta Pixel + Conversions API) is
wired today; ``ga4`` / ``tiktok`` are reserved so the storefront + admin can
grow to them without a schema change.
"""

from django.core.cache import cache
from django.db import models

from apps.core.models import TenantScopedModel


class TrackingProvider(models.TextChoices):
    META = "meta", "Meta Pixel"
    GA4 = "ga4", "Google Analytics 4"        # not implemented yet
    TIKTOK = "tiktok", "TikTok Pixel"        # not implemented yet


_CACHE_KEY = "marketing:tracking:{project_id}"
_CACHE_TTL = 600


class TrackingIntegration(TenantScopedModel):
    provider = models.CharField(max_length=16, choices=TrackingProvider.choices)

    # Meta: the Pixel ID.  GA4: the Measurement ID.
    pixel_id = models.CharField(max_length=64)
    # Server-side conversions token (Meta CAPI access token / GA4 API secret).
    server_token = models.CharField(max_length=512, blank=True)
    # Meta "Test events" code — events tagged with it show in the Test tab only.
    test_event_code = models.CharField(max_length=64, blank=True)

    track_browser = models.BooleanField(default=True)
    track_server = models.BooleanField(default=True)
    is_enabled = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "provider"], name="uniq_tracking_provider_per_project"
            )
        ]
        ordering = ["provider"]

    def __str__(self):
        return f"{self.get_provider_display()} · {self.project_id}"

    @property
    def server_ready(self):
        return bool(self.is_enabled and self.track_server and self.pixel_id and self.server_token)

    @property
    def browser_ready(self):
        return bool(self.is_enabled and self.track_browser and self.pixel_id)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        bust_tracking_cache(self.project_id)

    def delete(self, *args, **kwargs):
        pid = self.project_id
        super().delete(*args, **kwargs)
        bust_tracking_cache(pid)


def bust_tracking_cache(project_id):
    cache.delete(_CACHE_KEY.format(project_id=project_id))


def tracking_for(project):
    """``{provider: {...browser fields...}}`` for the storefront — cached, only
    the fields the browser snippet needs (never the server token)."""
    if project is None:
        return {}
    key = _CACHE_KEY.format(project_id=project.pk)
    cached = cache.get(key)
    if cached is not None:
        return cached
    out = {}
    for row in TrackingIntegration.objects.filter(project=project, is_enabled=True):
        if row.browser_ready:
            out[row.provider] = {"pixel_id": row.pixel_id}
    cache.set(key, out, _CACHE_TTL)
    return out
