"""Root URL configuration.

- ``/sd/``     Django's built-in admin (Django templates, staff only)
- ``/admin/``  custom Mission Control panel (Jinja2 + HTMX), staff only
- ``/api/v1/`` REST API — added in a later phase
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve as _serve_media

from apps.core import health
from apps.projects.views import domain_check

urlpatterns = [
    path("healthz/", health.healthz),
    path("readyz/", health.readyz),
    path("metrics", health.metrics),
    path(".well-known/sd-domain-check", domain_check),
    path("sd/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls", namespace="accounts")),
    path("admin/", include("apps.control.urls", namespace="control")),
    path("payments/", include("apps.payments.urls", namespace="payments")),
    path("shipping/", include("apps.shipping.urls", namespace="shipping")),
    path("api/", include("apps.api.urls", namespace="api")),
    path("demo/", include("apps.storefront.urls", namespace="storefront")),
    path("app/", include("apps.shopfront.urls", namespace="shopfront")),
    path("shop/", include("apps.storefront.ornza_urls", namespace="ornza")),
    path("", include("apps.cms.urls", namespace="cms")),
]

# Django serves /media/ only for local dev or when SERVE_MEDIA is set (direct
# app access with no nginx/CDN in front). django.conf.urls.static.static() is
# a no-op when DEBUG is off, so wire the route explicitly.
if settings.DEBUG or settings.SERVE_MEDIA:
    urlpatterns += [
        re_path(
            r"^%s(?P<path>.*)$" % settings.MEDIA_URL.lstrip("/"),
            _serve_media,
            {"document_root": settings.MEDIA_ROOT},
        ),
    ]
