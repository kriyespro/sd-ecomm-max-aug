#!/usr/bin/env python
"""Compile the site-wide Tailwind bundle(s) (everything that is *not* a storefront skin).

Output: one ``static/site/<theme>.css`` per entry in ``_BRAND_COLORS`` below
(``site.css`` is the original indigo palette; others are per-deployment color
themes picked at runtime by a brand's ``color_theme`` — see
``apps.core.brand.Brand.color_theme`` and ``DJANGO_PLATFORM_COLOR_THEME``).
Collected by ``collectstatic``, served by WhiteNoise, linked from
``templates/base.jinja`` and ``templates/storefront/base.jinja``
(see ``config.jinja2._site_css_href``). When a bundle is missing (dev, or a build
hiccup) those templates fall back to the Tailwind Play CDN, and the marketing
pages re-emit their inline ``tailwind.config`` (indigo only — the CDN fallback
is a dev convenience, not meant to match every theme) so dev still looks right.

Covers: the marketing landing + partners pages, Mission Control, the auth
screens, and the no-store storefront. Storefront skins have their own bundles
(``tools/build_tailwind_skins.py``).

No Node. Uses the Tailwind **standalone** CLI (bundles the ``forms`` plugin).
Override the binary with ``TAILWIND_BIN``.

    TAILWIND_BIN=./tailwindcss python tools/build_tailwind_site.py

The theme below is the single source of truth; it must stay in sync with the
inline ``tailwind.config`` fallback in ``templates/marketing/landing.jinja`` and
``templates/marketing/partners.jinja``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "templates"
OUT = ROOT / "static" / "site"
BIN = os.environ.get("TAILWIND_BIN", "tailwindcss")

# Every template that renders through templates/base.jinja or
# templates/storefront/base.jinja. Skins live under templates/shopfront/ and are
# built separately, so they are deliberately excluded.
CONTENT = [
    str(TPL / "*.jinja"),
    str(TPL / "marketing" / "**" / "*.jinja"),
    str(TPL / "control" / "**" / "*.jinja"),
    str(TPL / "accounts" / "**" / "*.jinja"),
    str(TPL / "storefront" / "**" / "*.jinja"),
]

# Mission Control (templates/control/base_control.jinja) builds its colour
# classes at render time by string-concatenating a hue name
# (`'bg-' ~ _hue ~ '-950'`), so Tailwind's content scan never sees them.
# "green" is Easy mode's sidebar tint (bg-green-600 etc, not just -950/-900
# like the rest — Easy mode is meant to look obviously different). "sky" /
# "amber" are platform_in_store / dgc_in_store — a platform admin / DGC
# currently working inside a specific store, lighter shades of their normal
# indigo/orange so it's obvious at a glance (see _chrome_theme).
_HUES = "indigo|orange|emerald|rose|slate|green|sky|amber"
# Emitted as JS RegExp literals (Tailwind's safelist wants real regexes, not
# strings). The `/` inside the opacity pattern is escaped for the literal.
_SAFELIST_JS = (
    "[\n"
    f"    {{ pattern: /^(bg|text|border|ring|from|to)-({_HUES})-(50|100|200|300|400|500|600|700|800|900|950)$/ }},\n"
    f"    {{ pattern: /^bg-({_HUES})-(900|950)\\/(60|70|80)$/ }},\n"
    f"    {{ pattern: /^(bg|text)-({_HUES})-(800|900|950)$/, variants: ['hover'] }},\n"
    "  ]"
)

# Every deployment's "brand" palette, keyed by Brand.color_theme (see
# apps/core/brand.py). "site" is the original platform (shopinaday.com)
# palette and must keep this exact name/output ("site/site.css") — other
# deployments pick their own key via DJANGO_PLATFORM_COLOR_THEME and get
# static/site/<key>.css instead. Same fontFamily/keyframes for every theme;
# only the "brand" color ramp differs.
_BRAND_COLORS = {
    "site": {  # shopinaday.com — indigo/violet
        "50": "#eef2ff", "100": "#e0e7ff", "200": "#c7d2fe",
        "300": "#a5b4fc", "400": "#818cf8", "500": "#6366f1",
        "600": "#4f46e5", "700": "#4338ca", "800": "#3730a3",
        "900": "#312e81",
    },
    "mnxstore": {  # mnxstore.com — teal, deliberately a different hue family
        "50": "#f0fdfa", "100": "#ccfbf1", "200": "#99f6e4",
        "300": "#5eead4", "400": "#2dd4bf", "500": "#14b8a6",
        "600": "#0d9488", "700": "#0f766e", "800": "#115e59",
        "900": "#134e4a",
    },
}

_SHARED_EXTEND = {
    "fontFamily": {
        "sans": ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        "display": ['"Space Grotesk"', "Inter", "sans-serif"],
    },
    "keyframes": {
        "float": {"0%,100%": {"transform": "translateY(0)"},
                  "50%": {"transform": "translateY(-10px)"}},
        "floatslow": {"0%,100%": {"transform": "translateY(0) rotate(0deg)"},
                      "50%": {"transform": "translateY(-16px) rotate(2deg)"}},
    },
    "animation": {
        "float": "float 6s ease-in-out infinite",
        "floatslow": "floatslow 9s ease-in-out infinite",
    },
}

_INPUT_CSS = "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n"


def _config_js(brand_colors):
    theme = {"extend": {**_SHARED_EXTEND, "colors": {"brand": brand_colors}}}
    return (
        f"module.exports = {{\n"
        f"  content: {json.dumps(CONTENT)},\n"
        f"  safelist: {_SAFELIST_JS},\n"
        f"  theme: {json.dumps(theme)},\n"
        f"  plugins: [require('@tailwindcss/forms')],\n"
        f"}};\n"
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, colors in _BRAND_COLORS.items():
        dest = OUT / f"{name}.css"
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / f"{name}.config.js"
            src = Path(tmp) / f"{name}.in.css"
            cfg.write_text(_config_js(colors))
            src.write_text(_INPUT_CSS)
            subprocess.run(
                [BIN, "-c", str(cfg), "-i", str(src), "-o", str(dest), "--minify"],
                check=True, cwd=ROOT,
            )
        print(f"  {name:<10} {dest.stat().st_size / 1024:6.1f} KiB  {dest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
