"""Chunked upload for large backup .zip restores.

A single giant multipart POST for a multi-hundred-MB backup can take
longer to transfer than Cloudflare's own edge-to-origin timeout (~100s on
Free/Pro) -- independent of how fast the view responds once the body has
fully arrived. nginx's own generous timeout/body-size carve-out for the
restore endpoints (deploy/nginx/mnxstore-ssl.conf) never even comes into
play if Cloudflare kills the request first, since that happens upstream of
nginx entirely. Slicing the upload into small chunks (a few MB each) keeps
every individual request's transfer time well under any such edge
timeout, on any Cloudflare plan, with no DNS/cert changes -- only the
*assembled* file on local disk is ever large.

Used by the owner (apps.control.backup_views) and platform-admin
(apps.control.store_views) restore-upload flows. The automatic daily
snapshot / safety-snapshot restores never go through this -- their
archive is already sitting on local disk (StoreBackupSnapshot.archive),
there's no large client upload involved for those at all.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from pathlib import Path

from django.conf import settings

CHUNK_UPLOAD_DIR = Path(settings.BASE_DIR) / "var" / "restore_uploads"
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def names_match(typed: str, actual: str) -> bool:
    """Is ``typed`` the confirm-your-store's-name text the restore screens
    ask for? Still an exact match (this gates a destructive wipe, not a
    search box) -- but tolerant of the differences a human typing a name
    they can only *see*, not copy, would never notice: a stray leading/
    trailing space (e.g. picked up from the onboarding form that set the
    name and was never stripped), or the same name typed vs. pasted coming
    out as different Unicode normalization forms for an accented
    character."""
    def norm(s: str) -> str:
        return unicodedata.normalize("NFC", (s or "").strip())

    return norm(typed) == norm(actual)


class ChunkUploadError(Exception):
    pass


def new_upload_id() -> str:
    return uuid.uuid4().hex


def _part_path(upload_id: str) -> Path:
    if not upload_id or not _UPLOAD_ID_RE.match(upload_id):
        raise ChunkUploadError("Invalid upload session.")
    CHUNK_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return CHUNK_UPLOAD_DIR / f"{upload_id}.part"


def append_chunk(upload_id: str, chunk_index: int, data: bytes, max_bytes: int) -> int:
    """Append ``data`` to the in-progress upload and return the total bytes
    written so far. Chunks are expected in order -- the client uploader
    sends them sequentially, awaiting each response before the next, so
    there's no reordering to handle. ``chunk_index == 0`` always starts a
    fresh file (covers a retried upload reusing the same tab)."""
    path = _part_path(upload_id)
    if chunk_index == 0 and path.exists():
        path.unlink()
    with open(path, "ab") as fh:
        fh.write(data)
    total = path.stat().st_size
    if total > max_bytes:
        path.unlink()
        raise ChunkUploadError("That file is too large.")
    return total


def finalize(upload_id: str, max_bytes: int) -> bytes:
    """Read back the fully-assembled upload and remove it from disk."""
    path = _part_path(upload_id)
    if not path.exists():
        raise ChunkUploadError("Upload session not found or expired — reload and try again.")
    if path.stat().st_size > max_bytes:
        path.unlink()
        raise ChunkUploadError("That file is too large.")
    data = path.read_bytes()
    path.unlink()
    return data


def abort(upload_id: str) -> None:
    try:
        _part_path(upload_id).unlink(missing_ok=True)
    except ChunkUploadError:
        pass


def purge_stale(max_age_seconds: int) -> int:
    """Delete .part files older than ``max_age_seconds`` -- an abandoned
    upload (tab closed mid-upload, browser crash) never calls finalize() or
    abort(), so nothing else ever cleans these up. Called from
    apps.control.tasks.purge_stale_restore_uploads_task."""
    import time

    if not CHUNK_UPLOAD_DIR.is_dir():
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for path in CHUNK_UPLOAD_DIR.glob("*.part"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
