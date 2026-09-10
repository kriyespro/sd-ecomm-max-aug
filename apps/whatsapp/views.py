"""WhatsApp Cloud API webhook — one endpoint for every store.

GET  = Meta's subscription handshake (``hub.challenge`` echoed when the
       ``hub.verify_token`` matches some store's account).
POST = delivery-status updates + inbound customer messages, routed to a store
       by ``metadata.phone_number_id``.
"""

import hashlib
import hmac
import json
import logging

from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .models import WAStatus, WhatsAppAccount, WhatsAppMessage, account_by_phone_number_id
from .services import record_inbound

logger = logging.getLogger(__name__)

# Cloud API status -> our status. Never downgrade (read > delivered > sent).
_RANK = {WAStatus.QUEUED: 0, WAStatus.ACCEPTED: 1, WAStatus.SENT: 2,
         WAStatus.DELIVERED: 3, WAStatus.READ: 4, WAStatus.FAILED: 5}
_STATUS_MAP = {"sent": WAStatus.SENT, "delivered": WAStatus.DELIVERED,
               "read": WAStatus.READ, "failed": WAStatus.FAILED}


@method_decorator(csrf_exempt, name="dispatch")
class WebhookView(View):
    def get(self, request):
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge", "")
        if mode == "subscribe" and token and WhatsAppAccount.objects.filter(
            verify_token=token
        ).exists():
            return HttpResponse(challenge, content_type="text/plain")
        return HttpResponseForbidden("verification failed")

    def post(self, request):
        raw = request.body
        try:
            data = json.loads(raw.decode())
        except (ValueError, UnicodeDecodeError):
            return HttpResponse(status=400)

        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                pnid = (value.get("metadata") or {}).get("phone_number_id")
                account = account_by_phone_number_id(pnid)
                if account is None:
                    continue
                if not _signature_ok(account, request, raw):
                    logger.warning("whatsapp webhook: bad signature for project %s",
                                   account.project_id)
                    continue
                for st in value.get("statuses", []):
                    _apply_status(account, st)
                for m in value.get("messages", []):
                    _apply_inbound(account, m)

        return JsonResponse({"ok": True})


def _signature_ok(account, request, raw):
    if not account.app_secret:
        return True  # not configured -> accept (routed by known phone_number_id)
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not sig.startswith("sha256="):
        return False
    expected = hmac.new(account.app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig[7:], expected)


def _apply_status(account, st):
    wamid = st.get("id")
    new = _STATUS_MAP.get(st.get("status"))
    if not wamid or new is None:
        return
    msg = WhatsAppMessage.objects.filter(project=account.project, wamid=wamid).first()
    if msg is None:
        return
    if _RANK.get(new, 0) <= _RANK.get(msg.status, 0) and new != WAStatus.FAILED:
        return
    msg.status = new
    if new == WAStatus.FAILED:
        errs = st.get("errors") or []
        if errs:
            msg.error = (errs[0].get("title") or errs[0].get("message") or "")[:255]
    msg.save(update_fields=["status", "error"])


def _apply_inbound(account, m):
    text = ""
    if m.get("type") == "text":
        text = (m.get("text") or {}).get("body", "")
    elif m.get("type") == "button":
        text = (m.get("button") or {}).get("text", "")
    record_inbound(account, from_number=m.get("from", ""), wamid=m.get("id", ""), text=text)
