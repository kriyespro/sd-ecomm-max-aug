"""Platform-managed training videos (YouTube), shown in the control panel by
topic and filtered to the viewer's role.

Only a platform admin edits these. Not tenant-scoped — the same library serves
every store's team plus the DGCs.
"""

import re

from django.core.cache import cache
from django.db import models

from apps.core.models import TimeStampedModel

_YT_RE = re.compile(
    r"(?:youtu\.be/|v=|/embed/|/shorts/|/live/|/v/)([A-Za-z0-9_-]{11})"
)
_CACHE_KEY = "learning:published:v1"
_CACHE_TTL = 300


class Audience(models.TextChoices):
    ALL = "all", "Everyone"
    DGC = "dgc", "DGC / growth consultant"
    OWNER = "owner", "Store owner"
    MANAGER = "manager", "Store manager"
    STAFF = "staff", "Store staff"


def extract_youtube_id(url_or_id):
    raw = (url_or_id or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", raw):
        return raw
    m = _YT_RE.search(raw)
    return m.group(1) if m else ""


class LearningVideo(TimeStampedModel):
    title = models.CharField(max_length=200)
    topic = models.CharField(max_length=80, db_index=True,
                             help_text="Videos are grouped under this heading.")
    description = models.TextField(blank=True)

    source_url = models.CharField(max_length=300, help_text="Paste the YouTube link.")
    youtube_id = models.CharField(max_length=20, blank=True, editable=False)
    duration_label = models.CharField(max_length=20, blank=True, help_text="e.g. “7 min”.")

    audience = models.JSONField(default=list, blank=True,
                                help_text="Which roles see this video.")
    order = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["topic", "order", "title"]

    def __str__(self):
        return f"{self.topic} · {self.title}"

    def save(self, *args, **kwargs):
        self.youtube_id = extract_youtube_id(self.source_url)
        super().save(*args, **kwargs)
        cache.delete(_CACHE_KEY)

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        cache.delete(_CACHE_KEY)

    @property
    def embed_url(self):
        return f"https://www.youtube-nocookie.com/embed/{self.youtube_id}" if self.youtube_id else ""

    @property
    def thumbnail_url(self):
        return f"https://i.ytimg.com/vi/{self.youtube_id}/hqdefault.jpg" if self.youtube_id else ""

    @property
    def audience_labels(self):
        m = dict(Audience.choices)
        return [m.get(a, a) for a in (self.audience or [])]


def _published():
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached
    rows = list(LearningVideo.objects.filter(is_published=True).exclude(youtube_id=""))
    cache.set(_CACHE_KEY, rows, _CACHE_TTL)
    return rows


def videos_for(audience_keys, *, topic=None):
    """Published videos visible to a viewer with ``audience_keys`` (a set), as
    ``[(topic, [video, …]), …]`` ordered."""
    keys = set(audience_keys or ())
    out = []
    for v in _published():
        aud = set(v.audience or [])
        if not aud or "all" in aud or (aud & keys):
            if topic is None or v.topic == topic:
                out.append(v)
    grouped = {}
    for v in out:
        grouped.setdefault(v.topic, []).append(v)
    return [(t, grouped[t]) for t in sorted(grouped)]


def audience_keys_for(user, project):
    """The role buckets a control-panel viewer falls into."""
    from apps.accounts.permissions import (
        is_platform_admin,
        is_platform_staff,
        store_role,
    )

    keys = set()
    role = store_role(user, project)
    if role:
        keys.add(role)
    if is_platform_admin(user):
        keys |= {"dgc", "owner", "manager", "staff"}
    elif is_platform_staff(user):
        keys.add("dgc")
    return keys
