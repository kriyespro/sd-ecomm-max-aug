"""Upload handling: validation, checksum de-dupe, image dimensions + thumbnails."""

import io
import logging
import posixpath
from hashlib import sha256

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile

from .models import AssetKind, MediaAsset

logger = logging.getLogger(__name__)


def shrink_image_field(field_file, *, target_kb, max_edge, marker=".sd"):
    """Re-encode an ``ImageField`` file to a compact WebP, in place.

    Call from a model's ``save()`` before ``super().save()``. No-op when: the
    field is empty, the file was already processed (its name carries ``marker``),
    Pillow is missing, the source cannot be read, or it is already small and
    web-friendly. On success the field points at ``<stem><marker>.webp`` and the
    caller still owns persisting the row.
    """
    if not field_file:
        return
    name = field_file.name or ""
    stem, _ext = posixpath.splitext(posixpath.basename(name))
    if stem.endswith(marker):
        return
    try:
        field_file.open("rb")
        raw = field_file.read()
    except (OSError, ValueError) as exc:
        logger.warning("shrink_image_field: cannot read %s: %s", name, exc)
        return
    finally:
        try:
            field_file.close()
        except Exception:  # noqa: BLE001
            pass
    try:
        from .optimize import process

        result = process(
            raw, target_bytes=target_kb * 1024, max_edge=max_edge,
            skip_under_bytes=target_kb * 1024,
        )
    except Exception as exc:  # noqa: BLE001 - a bad image must not block the save
        logger.warning("shrink_image_field: optimise failed for %s: %s", name, exc)
        return
    if result.get("skipped") or not result.get("main"):
        return
    field_file.save(f"{stem}{marker}.webp", ContentFile(result["main"]), save=False)

MAX_SIZE = 15 * 1024 * 1024  # 15 MB
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}
THUMBNAIL_SIZES = {"sm": 200, "md": 600, "lg": 1200}

# Extension allowlist. The uploaded content-type is attacker-controlled, so the
# file extension is what decides how a browser renders it off the media domain —
# an .svg / .html served same-origin is stored XSS.
ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".tiff",
    ".mp4", ".webm", ".mov", ".m4v",
    ".pdf", ".txt", ".csv",
}
_EXT_CONTENT_TYPE = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif",
    ".bmp": "image/bmp", ".tiff": "image/tiff",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".pdf": "application/pdf", ".txt": "text/plain", ".csv": "text/csv",
}


class MediaError(Exception):
    pass


def _safe_extension(name):
    import posixpath as _pp

    ext = _pp.splitext((name or "").lower())[1]
    if ext not in ALLOWED_EXTENSIONS:
        raise MediaError(
            "That file type can't be uploaded. Allowed: images, video, PDF, "
            "and plain-text/CSV."
        )
    return ext


def _kind_for(content_type):
    if content_type in IMAGE_TYPES or content_type.startswith("image/"):
        return AssetKind.IMAGE
    if content_type.startswith("video/"):
        return AssetKind.VIDEO
    if content_type in {"application/pdf"} or content_type.startswith("text/"):
        return AssetKind.DOCUMENT
    return AssetKind.OTHER


def store_upload(*, project, upload: UploadedFile, uploaded_by=None, folder="", alt="", title=""):
    if upload.size and upload.size > MAX_SIZE:
        raise MediaError(f"File exceeds the {MAX_SIZE // (1024 * 1024)}MB limit.")

    ext = _safe_extension(upload.name)

    raw = upload.read()
    digest = sha256(raw).hexdigest()

    existing = MediaAsset.objects.filter(project=project, checksum=digest).first()
    if existing is not None:
        return existing

    # Trust the extension, not the client-supplied content-type.
    content_type = _EXT_CONTENT_TYPE.get(ext, "application/octet-stream")
    kind = _kind_for(content_type)

    # An image extension whose bytes don't decode as an image is a smuggling
    # attempt (polyglot) — reject it.
    if kind == AssetKind.IMAGE:
        try:
            from PIL import Image

            with Image.open(io.BytesIO(raw)) as _im:
                _im.verify()
        except ImportError:
            pass
        except Exception as exc:
            raise MediaError("That image file is corrupt or not a real image.") from exc

    asset = MediaAsset(
        project=project, kind=kind, original_name=upload.name or "",
        content_type=content_type, size=len(raw), checksum=digest,
        folder=folder.strip("/"), alt=alt, title=title, uploaded_by=uploaded_by,
    )
    asset.file.save(upload.name or f"{digest[:16]}", ContentFile(raw), save=False)

    if kind == AssetKind.IMAGE:
        _process_image(asset, raw)

    asset.save()
    return asset


def _process_image(asset, raw):
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return
    try:
        with Image.open(io.BytesIO(raw)) as im:
            asset.width, asset.height = im.size
            im = im.convert("RGB") if im.mode in ("P", "RGBA") else im
            thumbs = {}
            base = asset.file.name.rsplit(".", 1)[0]
            for key, box in THUMBNAIL_SIZES.items():
                if asset.width and asset.width <= box and asset.height and asset.height <= box:
                    continue
                copy = im.copy()
                copy.thumbnail((box, box))
                buf = io.BytesIO()
                copy.save(buf, format="WEBP", quality=82)
                name = f"{base}_{key}.webp"
                asset.file.storage.save(name, ContentFile(buf.getvalue()))
                thumbs[key] = asset.file.storage.url(name)
            asset.thumbnails = thumbs
    except Exception:  # noqa: BLE001 - a bad image should not fail the upload
        pass


def delete_asset(asset):
    storage = asset.file.storage
    for url in (asset.thumbnails or {}).values():
        # best effort: derive name back from url is storage-specific; skip if unknown
        pass
    if asset.file:
        asset.file.delete(save=False)
    asset.delete()
