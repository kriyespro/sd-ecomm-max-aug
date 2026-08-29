"""Liveness / readiness probes for the container runtime.

``/healthz/``  — process is up (no dependency checks). Use for liveness.
``/readyz/``   — DB + cache reachable. Use for readiness / load-balancer.
"""

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse, JsonResponse


def metrics(request):
    """Prometheus exposition — gated by a shared token so it isn't public."""
    token = getattr(settings, "METRICS_TOKEN", "")
    if not token or request.headers.get("X-Metrics-Token") != token:
        return HttpResponse(status=404)
    from django_prometheus.exports import ExportToDjangoView

    return ExportToDjangoView(request)


def healthz(request):
    return JsonResponse({"status": "ok"})


def readyz(request):
    checks = {}
    ok = True

    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["db"] = f"error: {exc.__class__.__name__}"
        ok = False

    try:
        cache.set("readyz", "1", 5)
        checks["cache"] = "ok" if cache.get("readyz") == "1" else "error: readback"
        ok = ok and checks["cache"] == "ok"
    except Exception as exc:  # noqa: BLE001
        checks["cache"] = f"error: {exc.__class__.__name__}"
        ok = False

    return JsonResponse(
        {"status": "ok" if ok else "degraded", "checks": checks},
        status=200 if ok else 503,
    )
