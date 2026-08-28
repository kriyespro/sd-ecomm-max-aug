"""Serve the pre-built ORNZA static storefront at /shop/ (dev only), wired to the
API via an injected shim.

``html-ornza/`` is a self-contained multi-page static site. Each HTML page is
served with ``ornza-api.js`` injected before </body> — that shim routes
add-to-cart / cart drawer / checkout to /api/v1/ (store resolved from the Host,
e.g. ornza.localhost). Run ``manage.py seed_ornza`` once to create the matching
catalog. In production serve html-ornza/ from Nginx / a CDN with the same
one-line script injection.
"""

from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse
from django.views.static import serve as _serve

ORNZA_ROOT = (Path(settings.BASE_DIR) / "html-ornza").resolve()
_SHIM = '\n<script src="/shop/ornza-api.js"></script>\n</body>'


def serve_ornza(request, path=""):
    rel = path or "index.html"
    target = (ORNZA_ROOT / rel).resolve()
    if not str(target).startswith(str(ORNZA_ROOT)):
        raise Http404
    if target.is_dir():
        target = (target / "index.html").resolve()
        rel = f"{rel.rstrip('/')}/index.html".lstrip("/")

    if target.suffix.lower() in {".html", ".htm"} and target.is_file():
        html = target.read_text(encoding="utf-8-sig")
        html = html.replace("</body>", _SHIM, 1) if "</body>" in html else html + _SHIM
        return HttpResponse(html, content_type="text/html; charset=utf-8")

    return _serve(request, rel, document_root=str(ORNZA_ROOT), show_indexes=False)
