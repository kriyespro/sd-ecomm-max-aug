"""Shopify-style image optimisation.

Re-encode an uploaded image to a compact WebP, targeting a byte budget while
keeping perceived quality high, and produce a set of responsive renditions.

Pure functions — no Django model imports — so Celery tasks, management commands
and services can all reuse this. Input and output are raw ``bytes``.

``process()`` is the one-decode entry point: it may report ``skipped`` when the
upload is already small and web-friendly, so we neither burn CPU nor grow the
file. ``optimize()`` / ``renditions()`` are thin wrappers kept for callers that
want just one piece and never skip.
"""

import io
import logging

logger = logging.getLogger(__name__)

# Defaults. Overridable per call (settings wire through in apps.catalog.tasks).
DEFAULT_TARGET_BYTES = 200 * 1024
MAX_EDGE = 2048                       # phone cameras shoot 4000px+; clamp it
VARIANT_WIDTHS = (512, 1024, 2048)

# method=6 is ~2x the CPU of method=4 for a few % smaller output — not worth it
# on a shared box. A short quality probe beats a fine-grained ladder for cost.
_ENCODE_METHOD = 4
_QUALITY_PROBES = (82, 68, 56, 46)
_SMALL_PIXELS = 160_000              # below this, lossless often wins
_WEB_FORMATS = {"JPEG", "WEBP"}


def _register_extra_formats():
    """Best-effort HEIC/HEIF support (iPhone uploads). No-op if not installed.
    AVIF and the common formats are handled by Pillow itself."""
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except Exception as exc:  # noqa: BLE001
        logger.info("HEIC/HEIF support unavailable: %s", exc)


_register_extra_formats()


def _probe(raw):
    """(FORMAT, (w, h)) from the header only — no full decode."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(raw)) as im:
            return (im.format or "").upper(), im.size
    except Exception:  # noqa: BLE001
        return "", (0, 0)


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
    im.save(buf, format="WEBP", quality=quality, method=_ENCODE_METHOD, lossless=lossless)
    return buf.getvalue()


def _fit_budget(im, target_bytes):
    """Probe a few quality levels, keep the smallest that clears the budget (or
    the smallest overall). Returns (bytes, quality)."""
    small = im.width * im.height <= _SMALL_PIXELS
    best = None
    for q in _QUALITY_PROBES:
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


def process(raw, *, target_bytes=DEFAULT_TARGET_BYTES, max_edge=MAX_EDGE,
            skip_under_bytes=None, widths=VARIANT_WIDTHS, enhance=True):
    """Single decode → everything.

    Returns a dict. ``skipped=True`` means the upload is already small enough and
    in a web format: the caller should keep the original file untouched. Otherwise
    ``main`` holds the optimised WebP bytes and ``renditions`` maps width → bytes.
    """
    if skip_under_bytes is None:
        skip_under_bytes = target_bytes

    fmt, _size = _probe(raw)
    im, has_alpha = _load(raw)

    if (
        len(raw) <= skip_under_bytes
        and max(im.width, im.height) <= max_edge
        and fmt in _WEB_FORMATS
        and not has_alpha
    ):
        return {
            "skipped": True,
            "width": im.width, "height": im.height, "bytes": len(raw),
            "main": None, "renditions": {},
        }

    if max(im.size) > max_edge:
        im.thumbnail((max_edge, max_edge))
    if enhance:
        im = _enhance(im)

    main, quality = _fit_budget(im, target_bytes)

    rends = {}
    for width in widths:
        if width >= im.width:
            continue
        copy = im.copy()
        copy.thumbnail((width, width * 10))
        budget = max(24 * 1024, int(target_bytes * (width / im.width) ** 2))
        rends[width] = _fit_budget(copy, budget)[0]

    return {
        "skipped": False,
        "width": im.width, "height": im.height,
        "bytes": len(main), "quality": quality, "format": "webp",
        "main": main, "renditions": rends,
    }


def optimize(raw, *, target_bytes=DEFAULT_TARGET_BYTES, max_edge=MAX_EDGE, enhance=True):
    """Main rendition only, never skipped. ``{"data", "width", "height", "bytes",
    "quality", "format"}``."""
    r = process(raw, target_bytes=target_bytes, max_edge=max_edge,
                skip_under_bytes=0, widths=(), enhance=enhance)
    return {
        "data": r["main"], "width": r["width"], "height": r["height"],
        "bytes": r["bytes"], "quality": r["quality"], "format": "webp",
    }


def renditions(raw, *, widths=VARIANT_WIDTHS, target_bytes=DEFAULT_TARGET_BYTES, enhance=True):
    """Responsive set only, never skipped. ``{width_int: webp_bytes}``."""
    return process(raw, target_bytes=target_bytes, skip_under_bytes=0,
                   widths=widths, enhance=enhance)["renditions"]
