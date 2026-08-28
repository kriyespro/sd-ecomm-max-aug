"""Root URL configuration.

- ``/sd/``     Django's built-in admin (Django templates, staff only)
- ``/admin/``  custom Mission Control panel (Jinja2 + HTMX), staff only
- ``/api/v1/`` REST API — added in a later phase
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core import health

urlpatterns = [
    path("healthz/", health.healthz),
    path("readyz/", health.readyz),
    path("metrics", health.metrics),
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

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
