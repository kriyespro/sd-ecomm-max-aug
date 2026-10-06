"""The CRM's clock.

Django stores everything in UTC, but the team works in India time: "today's
calls" must reset at midnight IST (not 05:30), a follow-up "tomorrow" means the
next Indian day, and a call logged at 23:30 IST belongs to that evening's date.
Every date boundary in the CRM goes through here — never ``timezone.localdate()``.

Override with the ``CRM_TIME_ZONE`` setting / env var.
"""

import datetime as dt
from functools import lru_cache
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone


@lru_cache(maxsize=8)
def _zone(name):
    return ZoneInfo(name)


def biz_tz():
    return _zone(getattr(settings, "CRM_TIME_ZONE", "") or "Asia/Kolkata")


def biz_now(now=None):
    return (now or timezone.now()).astimezone(biz_tz())


def biz_today(now=None):
    """The current calendar date in business time (usable as a model default)."""
    return biz_now(now).date()


def to_biz(value):
    """An aware datetime in business time (naive values are taken as UTC); None passes through."""
    if value is None:
        return None
    if timezone.is_naive(value):
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(biz_tz())


def day_bounds(start, end=None):
    """Aware [start 00:00, end+1 00:00) in business time for an inclusive date range."""
    end = end or start
    tz = biz_tz()
    lo = dt.datetime.combine(start, dt.time.min, tzinfo=tz)
    hi = dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, tzinfo=tz)
    return lo, hi
