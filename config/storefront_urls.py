"""URLconf for requests whose Host is a store's own domain.

``apps.core.middleware.StorefrontHostMiddleware`` points ``request.urlconf`` here
so the storefront is served at ``/`` — clean URLs on the merchant's domain —
instead of under ``/app/``. Legacy ``/app/…`` links (old bookmarks, e-mails
rendered with the default URLconf) 301 to the root equivalent.

The shared routes below mirror :mod:`config.urls` so payments webhooks, the API,
login, sitemap/robots and the domain-check probe keep working on the custom
domain. Platform-only routes (``/sd/``, ``/admin/`` Mission Control, ``/metrics``)
are intentionally left off.
"""

from django.conf import settings
from django.urls import include, path, re_path
from django.views.generic import RedirectView
from django.views.static import serve as _serve_media

from apps.core import health
from apps.projects.views import domain_check

urlpatterns = [
    path("healthz/", health.healthz),
    path("readyz/", health.readyz),
    path(".well-known/sd-domain-check", domain_check),
    path("accounts/", include("apps.accounts.urls", namespace="accounts")),
    # Mission Control on the store's own domain — the Host resolves the store,
    # so the owner lands straight in it (no store picker).
    path("admin/", include("apps.control.urls", namespace="control")),
    path("payments/", include("apps.payments.urls", namespace="payments")),
    path("shipping/", include("apps.shipping.urls", namespace="shipping")),
    path("api/", include("apps.api.urls", namespace="api")),
    # Legacy /app/... -> clean root URL. 302 (not 301) so browsers don't cache
    # it hard if the mount ever changes again.
    path("app/", RedirectView.as_view(url="/", query_string=True)),
    path(
        "app/<path:rest>",
        RedirectView.as_view(url="/%(rest)s", query_string=True),
    ),
    path("", include("apps.cms.urls", namespace="cms")),
    path("", include("apps.shopfront.urls", namespace="shopfront")),
]

if settings.DEBUG or settings.SERVE_MEDIA:
    urlpatterns += [
        re_path(
            r"^%s(?P<path>.*)$" % settings.MEDIA_URL.lstrip("/"),
            _serve_media,
            {"document_root": settings.MEDIA_ROOT},
        ),
    ]
