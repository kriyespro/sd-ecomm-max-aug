"""Shared YouTube link parsing — used by any model that lets someone paste a
YouTube URL and store just the video id (``apps.cms.Banner``/``UGCVideo``,
``apps.catalog.Product``). Handles ``youtube.com/watch``, ``/shorts/``,
``/live/``, ``/embed/`` and ``youtu.be`` links.
"""

import re

YOUTUBE_ID_RE = re.compile(
    r"(?:youtube(?:-nocookie)?\.com/(?:shorts/|live/|embed/|watch\?(?:\S*&)?v=)"
    r"|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def extract_youtube_id(url):
    match = YOUTUBE_ID_RE.search(url or "")
    return match.group(1) if match else ""
