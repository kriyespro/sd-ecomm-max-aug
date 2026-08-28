"""Public payment endpoints.

The webhook receiver is unauthenticated (it's a gateway-to-server callback) and
CSRF-exempt — it is protected instead by the provider's signature, verified in
``services.handle_webhook``. The project is resolved from the Host header by
``ProjectResolverMiddleware``; the frontend never supplies it.
"""

import json

from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from . import services
from .providers import provider_keys


@method_decorator(csrf_exempt, name="dispatch")
class WebhookView(View):
    def post(self, request, provider):
        if provider not in provider_keys():
            return HttpResponseBadRequest("Unknown provider.")
        project = getattr(request, "project", None)
        if project is None:
            return HttpResponseBadRequest("Unresolved store.")
        try:
            services.handle_webhook(
                project=project,
                provider_key=provider,
                headers=request.headers,
                body=request.body,
            )
        except services.PaymentError as exc:
            # 400 tells the gateway to retry / flags a bad signature.
            return HttpResponseBadRequest(str(exc))
        return HttpResponse("ok")


@method_decorator(csrf_exempt, name="dispatch")
class VerifyCallbackView(View):
    """Frontend posts the gateway handshake here after checkout completes.

    Body: JSON ``{"payment_id": <int>, ...gateway fields...}``. The payment id is
    validated against the resolved project so a caller can't settle another
    store's payment.
    """

    def post(self, request):
        project = getattr(request, "project", None)
        if project is None:
            return HttpResponseBadRequest("Unresolved store.")
        try:
            data = json.loads(request.body or b"{}")
        except ValueError:
            return HttpResponseBadRequest("Invalid JSON.")

        from .models import Payment

        try:
            payment = Payment.objects.get(pk=data.get("payment_id"), project=project)
        except (Payment.DoesNotExist, ValueError, TypeError):
            return HttpResponseBadRequest("Unknown payment.")

        try:
            services.verify_payment(payment=payment, data=data)
        except services.PaymentError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        return JsonResponse({"ok": True, "order": payment.order.number})
