#!/usr/bin/env python
"""Compile the site-wide Tailwind bundle (everything that is *not* a storefront skin).

Output: ``static/site/site.css`` — collected by ``collectstatic``, served by
WhiteNoise, linked from ``templates/base.jinja`` and ``templates/storefront/base.jinja``
(see ``config.jinja2._site_css_href``). When the file is missing (dev, or a build
hiccup) those templates fall back to the Tailwind Play CDN, and the marketing
pages re-emit their inline ``tailwind.config`` so dev still looks right.

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
_HUES = "indigo|orange|emerald|rose|slate"
# Emitted as JS RegExp literals (Tailwind's safelist wants real regexes, not
# strings). The `/` inside the opacity pattern is escaped for the literal.
_SAFELIST_JS = (
    "[\n"
    f"    {{ pattern: /^(bg|text|border|ring|from|to)-({_HUES})-(50|100|200|300|400|500|600|700|800|900|950)$/ }},\n"
    f"    {{ pattern: /^bg-({_HUES})-(900|950)\\/(60|70|80)$/ }},\n"
    f"    {{ pattern: /^(bg|text)-({_HUES})-(800|900|950)$/, variants: ['hover'] }},\n"
    "  ]"
)

THEME = {
    "extend": {
        "fontFamily": {
            "sans": ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
            "display": ['"Space Grotesk"', "Inter", "sans-serif"],
        },
        "colors": {
            "brand": {
                "50": "#eef2ff", "100": "#e0e7ff", "200": "#c7d2fe",
                "300": "#a5b4fc", "400": "#818cf8", "500": "#6366f1",
                "600": "#4f46e5", "700": "#4338ca", "800": "#3730a3",
                "900": "#312e81",
            },
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
    },
}

_CONFIG_JS = (
    f"module.exports = {{\n"
    f"  content: {json.dumps(CONTENT)},\n"
    f"  safelist: {_SAFELIST_JS},\n"
    f"  theme: {json.dumps(THEME)},\n"
    f"  plugins: [require('@tailwindcss/forms')],\n"
    f"}};\n"
)
_INPUT_CSS = "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / "site.css"
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "site.config.js"
        src = Path(tmp) / "site.css"
        cfg.write_text(_CONFIG_JS)
        src.write_text(_INPUT_CSS)
        subprocess.run(
            [BIN, "-c", str(cfg), "-i", str(src), "-o", str(dest), "--minify"],
            check=True, cwd=ROOT,
        )
    print(f"  site  {dest.stat().st_size / 1024:6.1f} KiB  {dest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
