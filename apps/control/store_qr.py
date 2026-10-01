"""Printable store QR code.

A QR that opens the store is only offered once the store has its own *custom*
domain — the auto ``<slug>.<platform>`` subdomain can change (the owner can
rename it in the setup wizard), and a printed QR must never go stale.
"""

import io
import os
import re

import qrcode
from PIL import Image, ImageDraw, ImageFont
from qrcode.constants import ERROR_CORRECT_H

from apps.projects.models import _is_platform_host

CARD_WIDTH = 2400          # px; 8 in at 300 dpi
QR_AREA = 1800             # px; scannable square, quiet zone included
DPI = 300
INK = (15, 23, 42)         # slate-900
MUTED = (100, 116, 139)    # slate-500

# Pillow's bundled font covers Latin only; prefer a system face with wider
# coverage (Devanagari etc.) when the host has one.
_BOLD_FONTS = (
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
)
_REGULAR_FONTS = (
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
)


def custom_domain_url(project):
    """``https://<host>/`` of the store's own (non-platform) domain, or ``None``.

    A verified ``Domain`` row, or ``primary_domain`` (the same hosts
    ``StorefrontHostMiddleware`` routes), that isn't the platform itself or one
    of its subdomains. The primary domain wins.
    """
    verified = sorted(
        (d for d in project.domains.all() if d.is_verified),
        key=lambda d: (not d.is_primary, d.created_at),
    )
    hosts = [d.host.strip().lower() for d in verified]
    pd = (project.primary_domain or "").strip().lower()
    if pd and pd not in hosts:
        hosts.append(pd)
    for host in hosts:
        if host and not _is_platform_host(host):
            return f"https://{host}/"
    return None


def _font(candidates, size):
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def _text_width(draw, text, font):
    left, _, right, _ = draw.textbbox((0, 0), text, font=font)
    return right - left


def _fit_name(draw, name, max_width):
    """Largest bold size (<=170px) that fits on one line; else wrap to two
    lines at the smallest size, truncating with an ellipsis if still too long."""
    for size in range(170, 79, -10):
        font = _font(_BOLD_FONTS, size)
        if _text_width(draw, name, font) <= max_width:
            return font, [name]
    font = _font(_BOLD_FONTS, 80)
    lines, current = [], ""
    for word in name.split():
        trial = f"{current} {word}".strip()
        if _text_width(draw, trial, font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    lines.append(current)
    lines = lines[:2]
    for i, line in enumerate(lines):
        while len(line) > 1 and _text_width(draw, line, font) > max_width:
            line = line[:-2].rstrip() + "…"
        lines[i] = line
    return font, lines


def render_png(url, store_name):
    """A print-ready PNG card: "SCAN TO SHOP", the QR, the store name, the host.

    High (H) error correction + the QR kept free of any logo overlay, so it
    scans reliably even when printed small or slightly worn. Module size is an
    integer number of pixels, so edges are razor sharp at any print size.
    """
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, border=4, box_size=1)
    qr.add_data(url)
    qr.make(fit=True)
    total = qr.modules_count + 2 * qr.border
    box = max(1, QR_AREA // total)
    qr.box_size = box
    code = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    name = " ".join((store_name or "").split()) or "Our store"
    name_font, name_lines = _fit_name(probe, name, CARD_WIDTH - 360)
    head_font = _font(_REGULAR_FONTS, 78)
    host_font = _font(_REGULAR_FONTS, 74)
    host = re.sub(r"^https?://", "", url).rstrip("/")

    head_h, gap, name_lh, foot = 90, 60, int(name_font.size * 1.25), 150
    height = 150 + head_h + gap + code.height + gap + name_lh * len(name_lines) + 40 + 90 + foot
    card = Image.new("RGB", (CARD_WIDTH, height), "white")
    draw = ImageDraw.Draw(card)

    def centered(text, font, y, fill, stroke=0):
        w = _text_width(draw, text, font)
        draw.text(((CARD_WIDTH - w) // 2, y), text, font=font, fill=fill,
                  stroke_width=stroke, stroke_fill=fill)

    y = 150
    centered("SCAN TO SHOP", head_font, y, MUTED, stroke=1)
    y += head_h + gap
    card.paste(code, ((CARD_WIDTH - code.width) // 2, y))
    y += code.height + gap
    for line in name_lines:
        # stroke fakes bold when only the (regular-weight) bundled font exists
        centered(line, name_font, y, INK, stroke=max(1, name_font.size // 45))
        y += name_lh
    centered(host, host_font, y + 40, MUTED)

    buf = io.BytesIO()
    card.save(buf, format="PNG", dpi=(DPI, DPI), optimize=True)
    return buf.getvalue()
