"""Inline SVG placeholders for image slots a store owner hasn't filled yet.

`svg_placeholder(w, h, label)` returns a `data:image/svg+xml,...` URI — a flat
grey box with the ideal pixel size centred on it (and an optional slot label
below), so a new storefront reads as "complete, drop your images here" instead
of broken or empty. No files, no requests, works offline.

Wired into templates as the `placeholder` global and the `media_src` filter in
`config/jinja2.py`.
"""

from __future__ import annotations

from urllib.parse import quote

_BG = "#dcdcdc"
_FG = "#8c8c8c"
_FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
)

_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
    'viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid slice" role="img" '
    'aria-label="{aria}">'
    '<rect width="{w}" height="{h}" fill="{bg}"/>'
    '<text x="50%" y="50%" fill="{fg}" font-family="{font}" font-size="{fs}" '
    'font-weight="700" text-anchor="middle" '
    'dy="{dy}">{w}×{h}</text>'
    "{label}"
    "</svg>"
)
_LABEL = (
    '<text x="50%" y="50%" fill="{fg}" font-family="{font}" font-size="{lfs}" '
    'font-weight="400" letter-spacing="1.5" text-anchor="middle" '
    'dy="{ldy}">{text}</text>'
)


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def svg_placeholder(w, h, label: str = "", *, bg: str = _BG, fg: str = _FG) -> str:
    """Data-URI SVG: a `bg` rectangle `w`x`h` with "W×H" (and `label`) in `fg`."""
    try:
        w, h = int(round(float(w))), int(round(float(h)))
    except (TypeError, ValueError):
        w, h = 1200, 800
    w, h = max(w, 1), max(h, 1)

    fs = max(13, min(round(min(w, h) / 7), 96))
    label = (label or "").strip()
    lfs = max(10, round(fs * 0.42))

    if label:
        label_el = _LABEL.format(
            fg=fg, font=_FONT, lfs=lfs, ldy=round(fs * 0.62 + lfs),
            text=_esc(label.upper()),
        )
        dy = f"-{round(fs * 0.15)}"
    else:
        label_el = ""
        dy = "0.35em"

    aria = _esc(f"{label + ' ' if label else ''}placeholder {w} by {h} pixels")
    svg = _SVG.format(
        w=w, h=h, bg=bg, fg=fg, font=_FONT, fs=fs, dy=dy, aria=aria, label=label_el,
    )
    return "data:image/svg+xml;charset=utf-8," + quote(svg, safe="")


def media_src(value, w=1200, h=800, label: str = "") -> str:
    """Template helper: real media URL when `value` is set, else a placeholder.

    `value` may be an `ImageFieldFile` (truthy only when a file is attached), a
    plain URL string, or falsy. Used as a Jinja filter — see `config/jinja2.py`.
    """
    if value:
        url = getattr(value, "url", None)
        if url:
            return url
        if isinstance(value, str):
            return value
    return svg_placeholder(w, h, label)
