"""Take a store's storefront offline when its subscription is suspended.

Only the public storefronts (``/app/``, ``/demo/``, ``/shop/``) are gated — the
owner must still reach Mission Control to pay the outstanding invoice.
"""

from django.http import HttpResponse

_STOREFRONT_PREFIXES = ("/app/", "/demo/", "/shop/")

_SUSPENDED_HTML = (
    "<!doctype html><html><head><title>Store unavailable</title></head>"
    "<body style='font-family:system-ui;text-align:center;padding:15vh 1rem'>"
    "<h1>This store is temporarily unavailable</h1>"
    "<p>Please check back soon.</p></body></html>"
)


class SubscriptionGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(_STOREFRONT_PREFIXES):
            project = getattr(request, "project", None)
            if project is not None:
                sub = getattr(project, "subscription", None)
                if sub is not None and sub.status == "suspended":
                    return HttpResponse(_SUSPENDED_HTML, status=503,
                                        content_type="text/html")
        return self.get_response(request)
