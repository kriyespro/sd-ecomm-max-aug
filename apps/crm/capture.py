"""Public webhook for inbound leads from outside the app (Meta / Google lead
forms via Zapier-style tools, a website form, a spreadsheet script…).

Off until ``CRM_CAPTURE_TOKEN`` is set. POST JSON or form fields:
name, phone, business, city, source, notes (+ any utm_* / gclid / fbclid).
Send the token as ``X-CRM-Token: <token>`` or ``Authorization: Bearer <token>``.
A duplicate phone changes nothing and says so; a new lead is routed to a DGC
instantly (see services.route_lead).
"""

import hmac
import json

from django.conf import settings
from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import services as svc

_MAX_BODY = 10 * 1024
_RATE = 120          # requests per minute per client IP
_ACQ_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "gclid", "fbclid", "lp", "form")


def _client_ip(request):
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _token_ok(request, expected):
    given = request.headers.get("X-CRM-Token", "")
    if not given:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            given = auth[7:].strip()
    return bool(given) and hmac.compare_digest(given.encode(), expected.encode())


@csrf_exempt
@require_POST
def capture_lead(request):
    expected = getattr(settings, "CRM_CAPTURE_TOKEN", "")
    if not expected:
        raise Http404                      # feature is off unless a token is configured
    key = f"crm_capture:{_client_ip(request)}"
    hits = cache.get(key, 0)
    if hits >= _RATE:
        return JsonResponse({"ok": False, "error": "rate limited"}, status=429)
    cache.set(key, hits + 1, 60)
    if not _token_ok(request, expected):
        return JsonResponse({"ok": False, "error": "bad token"}, status=403)

    if len(request.body) > _MAX_BODY:
        return JsonResponse({"ok": False, "error": "payload too large"}, status=413)
    if (request.content_type or "").startswith("application/json"):
        try:
            data = json.loads(request.body or b"{}")
        except ValueError:
            return JsonResponse({"ok": False, "error": "invalid JSON"}, status=400)
        if not isinstance(data, dict):
            return JsonResponse({"ok": False, "error": "expected an object"}, status=400)
    else:
        data = request.POST.dict()

    def text(k, n):
        v = data.get(k, "")
        return str(v).strip()[:n] if v is not None else ""

    acq = {k: text(k, 120) for k in _ACQ_KEYS if text(k, 120)}
    try:
        lead, created = svc.ingest_lead(
            name=text("name", 120), phone=text("phone", 20), business=text("business", 160),
            city=text("city", 80), source=text("source", 60) or "capture", notes=text("notes", 2000),
            acquisition=acq)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({
        "ok": True, "created": created, "lead_id": lead.pk if lead else None,
        "assigned": svc.person_label(lead.assigned_to) if (lead and lead.assigned_to_id) else None,
    }, status=201 if created else 200)
