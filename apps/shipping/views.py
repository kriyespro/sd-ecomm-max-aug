"""Public courier webhook receiver.

Unauthenticated + CSRF-exempt (courier-to-server callback); protected by the
courier's signature, verified in ``services.handle_courier_webhook``. Project is
resolved from the Host header by ``ProjectResolverMiddleware``.
"""

from django.http import HttpResponse, HttpResponseBadRequest
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from . import services
from .couriers import courier_keys


@method_decorator(csrf_exempt, name="dispatch")
class CourierWebhookView(View):
    def post(self, request, courier):
        if courier not in courier_keys():
            return HttpResponseBadRequest("Unknown courier.")
        project = getattr(request, "project", None)
        if project is None:
            return HttpResponseBadRequest("Unresolved store.")
        try:
            services.handle_courier_webhook(
                project=project, courier_key=courier,
                headers=request.headers, body=request.body,
            )
        except services.ShippingError as exc:
            return HttpResponseBadRequest(str(exc))
        return HttpResponse("ok")
