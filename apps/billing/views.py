"""Public billing endpoint: the Razorpay webhook for platform subscription
invoices. Unauthenticated (gateway -> server) and CSRF-exempt — protected by
the Razorpay signature, verified in ``services.handle_billing_webhook``.
"""

from django.http import HttpResponse, HttpResponseBadRequest
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from . import services


@method_decorator(csrf_exempt, name="dispatch")
class BillingWebhookView(View):
    def post(self, request):
        try:
            services.handle_billing_webhook(headers=request.headers, body=request.body)
        except services.BillingError as exc:
            # 400 -> Razorpay retries; also flags a bad signature.
            return HttpResponseBadRequest(str(exc))
        return HttpResponse("ok")
