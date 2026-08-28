"""Upload handling: validation, checksum de-dupe, image dimensions + thumbnails."""

import io
from hashlib import sha256

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile

from .models import AssetKind, MediaAsset

MAX_SIZE = 15 * 1024 * 1024  # 15 MB
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}
THUMBNAIL_SIZES = {"sm": 200, "md": 600, "lg": 1200}


class MediaError(Exception):
    pass


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

    raw = upload.read()
    digest = sha256(raw).hexdigest()

    existing = MediaAsset.objects.filter(project=project, checksum=digest).first()
    if existing is not None:
        return existing

    content_type = getattr(upload, "content_type", "") or "application/octet-stream"
    kind = _kind_for(content_type)

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
