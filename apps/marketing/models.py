"""Third-party marketing / conversion tracking.

``TrackingIntegration`` — one row per (store, provider): Meta Pixel + Conversions
API, GA4, TikTok Pixel + Events API. Configured by the store's own team.

``PlatformTrackingSettings`` — a singleton for the platform marketing site
(shopinaday.com landing / pricing / signup), configured by a superadmin. Browser
pixels only; no server-side / PII path.
"""

from django.core.cache import cache
from django.db import models

from apps.core.models import TenantScopedModel, TimeStampedModel


class TrackingProvider(models.TextChoices):
    META = "meta", "Meta Pixel"
    GA4 = "ga4", "Google Analytics 4"
    TIKTOK = "tiktok", "TikTok Pixel"


_CACHE_KEY = "marketing:tracking:{project_id}"
_CACHE_TTL = 600
_PLATFORM_CACHE_KEY = "marketing:platform-tracking"


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


class PlatformTrackingSettings(TimeStampedModel):
    """Singleton — pixels for the platform's own marketing site."""

    meta_pixel_id = models.CharField(max_length=64, blank=True)
    ga4_measurement_id = models.CharField(max_length=32, blank=True)
    tiktok_pixel_id = models.CharField(max_length=64, blank=True)
    is_enabled = models.BooleanField(
        default=False, help_text="Master switch — off = inject nothing.",
    )

    # Server-side: fires a Meta Conversions API "CompleteRegistration" event
    # when a visitor finishes public self-signup (a new trial store). Browser
    # pixel above stays PageView-only; this is the one funnel event worth
    # sending server-side (ad-blockers hide the browser one most often).
    meta_capi_token = models.CharField(
        "Meta Conversions API access token", max_length=512, blank=True,
        help_text="Events Manager → your pixel → Settings → Conversions API "
                  "→ Generate access token.",
    )
    meta_test_event_code = models.CharField(max_length=64, blank=True)

    class Meta:
        verbose_name = "platform tracking settings"
        verbose_name_plural = "platform tracking settings"

    def __str__(self):
        return "Platform tracking settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(_PLATFORM_CACHE_KEY)

    def as_map(self):
        """``{provider: {"pixel_id": id}}`` for the enabled, filled-in ones."""
        if not self.is_enabled:
            return {}
        pairs = (("meta", self.meta_pixel_id), ("ga4", self.ga4_measurement_id),
                 ("tiktok", self.tiktok_pixel_id))
        return {p: {"pixel_id": v.strip()} for p, v in pairs if v.strip()}

    @property
    def capi_ready(self):
        return bool(self.is_enabled and self.meta_pixel_id and self.meta_capi_token)


def platform_tracking():
    """Cached ``{provider: {"pixel_id": id}}`` for the marketing site."""
    cached = cache.get(_PLATFORM_CACHE_KEY)
    if cached is not None:
        return cached
    out = PlatformTrackingSettings.load().as_map()
    cache.set(_PLATFORM_CACHE_KEY, out, _CACHE_TTL)
    return out
