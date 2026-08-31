#!/usr/bin/env python
"""Compile one Tailwind bundle per storefront skin.

Output: ``static/shopfront/skins/<slug>.css`` — picked up by ``collectstatic``
and served by WhiteNoise. The skin ``base.jinja`` links it when it exists and
otherwise falls back to the Tailwind Play CDN (see ``config/jinja2._skin_css_href``).

No Node. Uses the Tailwind **standalone** CLI (the ``tailwindcss`` binary),
which bundles the ``forms`` plugin. Override the binary with ``TAILWIND_BIN``.

    TAILWIND_BIN=./tailwindcss python tools/build_tailwind_skins.py [slug ...]

Each skin's palette + fonts come straight out of its generated ``base.jinja``
``tailwind.config`` block, so this never drifts from ``manage.py seed_skins``.
The per-store accent colour is dynamic, so ``accent`` compiles to
``rgb(var(--accent) / <alpha-value>)`` and ``--accent`` is set at render time.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKINS = ROOT / "templates" / "shopfront" / "skins"
SHOPFRONT_TPL = ROOT / "templates" / "shopfront"
OUT = ROOT / "static" / "shopfront" / "skins"
BIN = os.environ.get("TAILWIND_BIN", "tailwindcss")

_CONFIG_RE = re.compile(r"tailwind\.config\s*=\s*(\{.*?\});", re.DOTALL)
_ACCENT_RE = re.compile(r"'\{\{ accent or \"#[0-9a-fA-F]+\" \}\}'")
_INPUT_CSS = "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n"


def skin_slugs() -> list[str]:
    return sorted(
        p.name for p in SKINS.iterdir()
        if p.is_dir() and (p / "base.jinja").exists()
    )


def _content_globs(slug: str) -> list[str]:
    # Every template a skin can actually render: its own folder, the default
    # skin it falls back to, and the shared shopfront partials.
    return [
        str(SKINS / slug / "**" / "*.jinja"),
        str(SKINS / "default" / "**" / "*.jinja"),
        str(SHOPFRONT_TPL / "*.jinja"),
        str(SHOPFRONT_TPL / "partials" / "**" / "*.jinja"),
    ]


def _config_js(slug: str) -> str:
    base = (SKINS / slug / "base.jinja").read_text()
    m = _CONFIG_RE.search(base)
    if not m:
        raise SystemExit(f"{slug}: no `tailwind.config` block in base.jinja")
    ext = _ACCENT_RE.sub("'rgb(var(--accent) / <alpha-value>)'", m.group(1))
    return (
        f"const ext = {ext};\n"
        "module.exports = {\n"
        f"  content: {json.dumps(_content_globs(slug))},\n"
        "  theme: ext.theme,\n"
        "  plugins: [require('@tailwindcss/forms')],\n"
        "};\n"
    )


def build(slug: str, workdir: Path) -> None:
    cfg = workdir / f"{slug}.config.js"
    src = workdir / f"{slug}.css"
    cfg.write_text(_config_js(slug))
    src.write_text(_INPUT_CSS)
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"{slug}.css"
    subprocess.run(
        [BIN, "-c", str(cfg), "-i", str(src), "-o", str(dest), "--minify"],
        check=True, cwd=ROOT,
    )
    kb = dest.stat().st_size / 1024
    print(f"  {slug:10} {kb:6.1f} KiB  {dest.relative_to(ROOT)}")


def main(argv: list[str]) -> int:
    wanted = argv or skin_slugs()
    unknown = set(wanted) - set(skin_slugs())
    if unknown:
        raise SystemExit(f"unknown skin(s): {', '.join(sorted(unknown))}")
    with tempfile.TemporaryDirectory() as tmp:
        for slug in wanted:
            build(slug, Path(tmp))
    print(f"{len(wanted)} skin CSS bundle(s) written to {OUT.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
