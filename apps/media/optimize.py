"""Shopify-style image optimisation.

Re-encode any uploaded image to a compact WebP, targeting a byte budget while
keeping perceived quality high, and produce a set of responsive renditions.

Pure functions — no Django model imports — so Celery tasks, management commands
and services can all reuse this. Input and output are raw ``bytes``.
"""

import io
import logging

logger = logging.getLogger(__name__)

# Defaults. Overridable per call (settings wire through in apps.catalog.tasks).
DEFAULT_TARGET_BYTES = 200 * 1024
MAX_EDGE = 2048                       # phone cameras shoot 4000px+; clamp it
VARIANT_WIDTHS = (512, 1024, 2048)

_START_QUALITY = 88
_MIN_QUALITY = 46
_QUALITY_STEP = 6
_SMALL_PIXELS = 160_000              # below this, lossless often wins


def _register_extra_formats():
    """Best-effort HEIC/HEIF support (iPhone uploads). No-op if not installed.
    AVIF and the common formats are handled by Pillow itself."""
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except Exception as exc:  # noqa: BLE001
        logger.info("HEIC/HEIF support unavailable: %s", exc)


_register_extra_formats()


def _load(raw):
    """Return (RGB/RGBA PIL image, has_alpha). Applies EXIF orientation."""
    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(raw))
    im = ImageOps.exif_transpose(im) or im
    has_alpha = im.mode in ("RGBA", "LA") or (
        im.mode == "P" and "transparency" in im.info
    )
    return im.convert("RGBA" if has_alpha else "RGB"), has_alpha


def _enhance(im):
    """Gentle, Shopify-like touch-up so the smaller file still reads as crisp:
    a light unsharp mask. Cheap and safe across photo content."""
    from PIL import ImageFilter

    return im.filter(ImageFilter.UnsharpMask(radius=1.0, percent=55, threshold=3))


def _encode(im, quality, lossless=False):
    buf = io.BytesIO()
    im.save(buf, format="WEBP", quality=quality, method=6, lossless=lossless)
    return buf.getvalue()


def _fit_budget(im, target_bytes):
    """Encode WebP, stepping quality down until under budget. Returns (bytes,
    quality). Tries lossless when the image is small or lossy stays over."""
    small = im.width * im.height <= _SMALL_PIXELS
    best = None
    for q in range(_START_QUALITY, _MIN_QUALITY - 1, -_QUALITY_STEP):
        data = _encode(im, q)
        if best is None or len(data) < len(best[0]):
            best = (data, q)
        if len(data) <= target_bytes:
            break

    if small or len(best[0]) > target_bytes:
        lossless = _encode(im, 100, lossless=True)
        if len(lossless) < len(best[0]):
            best = (lossless, 100)
    return best


def optimize(raw, *, target_bytes=DEFAULT_TARGET_BYTES, max_edge=MAX_EDGE, enhance=True):
    """Main rendition: clamped to ``max_edge``, squeezed under ``target_bytes``.

    Returns ``{"data", "width", "height", "bytes", "quality", "format"}``.
    """
    im, _ = _load(raw)
    if max(im.size) > max_edge:
        im.thumbnail((max_edge, max_edge))
    if enhance:
        im = _enhance(im)

    data, quality = _fit_budget(im, target_bytes)
    return {
        "data": data,
        "width": im.width,
        "height": im.height,
        "bytes": len(data),
        "quality": quality,
        "format": "webp",
    }


def renditions(raw, *, widths=VARIANT_WIDTHS, target_bytes=DEFAULT_TARGET_BYTES, enhance=True):
    """Responsive set. Returns ``{width_int: webp_bytes}`` for every width not
    larger than the source. Byte budget scales with width so small renditions
    stay small."""
    im, _ = _load(raw)
    if enhance:
        im = _enhance(im)

    out = {}
    for w in widths:
        if w >= im.width:
            continue
        copy = im.copy()
        copy.thumbnail((w, w * 10))
        budget = max(24 * 1024, int(target_bytes * (w / im.width) ** 2))
        data, _q = _fit_budget(copy, budget)
        out[w] = data
    return out
