"""Per-store OpenRouter API keys used to power AI assist features (product
copywriting first). Each store owner/manager pastes their own key(s) — up to
``MAX_KEYS_PER_STORE`` — free of charge to the platform: only OpenRouter's
free-tier (":free") models are ever called. Several keys let the rotation in
``apps/ai/services.py`` spread requests so no single key gets rate-limited or
flagged for hammering the free tier.
"""

from django.db import models

from apps.core.models import TenantScopedModel

MAX_KEYS_PER_STORE = 10


class AiProviderKey(TenantScopedModel):
    label = models.CharField(max_length=60, blank=True)
    api_key = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    last_used_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)
    request_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.label or self.masked_key

    @property
    def masked_key(self):
        k = self.api_key or ""
        if len(k) <= 10:
            return "•" * len(k)
        return f"{k[:8]}…{k[-4:]}"
