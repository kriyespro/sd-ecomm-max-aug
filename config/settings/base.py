"""Base settings shared by every environment.

Environment-specific modules (``development``, ``production``) import ``*`` from
here and override what they need. Never import this module directly as
``DJANGO_SETTINGS_MODULE``.
"""

from pathlib import Path

from dotenv import load_dotenv
import os

# config/settings/base.py -> config/settings -> config -> project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR / ".env")


def env(key, default=None):
    return os.environ.get(key, default)


def env_bool(key, default=False):
    val = os.environ.get(key)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def env_list(key, default=None):
    val = os.environ.get(key)
    if not val:
        return list(default or [])
    return [item.strip() for item in val.split(",") if item.strip()]


SECRET_KEY = env("DJANGO_SECRET_KEY", "django-insecure-dev-only-change-me")

DEBUG = env_bool("DJANGO_DEBUG", False)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1"])


# --- Applications ---------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework.authtoken",
    "django_prometheus",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.projects",
    "apps.billing",
    "apps.control",
    "apps.categories",
    "apps.catalog",
    "apps.inventory",
    "apps.cart",
    "apps.orders",
    "apps.checkout",
    "apps.payments",
    "apps.shipping",
    "apps.customers",
    "apps.coupons",
    "apps.reviews",
    "apps.wishlist",
    "apps.cms",
    "apps.seo",
    "apps.api",
    "apps.notifications",
    "apps.webhooks",
    "apps.media",
    "apps.analytics",
    "apps.storefront",
    "apps.shopfront",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS


MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.RealClientIPMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Resolves request.project from the Host header. Must run after auth so it
    # can fall back to the authenticated user's memberships.
    "apps.core.middleware.ProjectResolverMiddleware",
    "apps.shopfront.middleware.StorefrontSkinMiddleware",
    "apps.shopfront.middleware.NoStoreStorefrontMiddleware",
    "apps.billing.middleware.SubscriptionGateMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]

# /metrics is only served to a request that presents this token in
# X-Metrics-Token (set it in prod; empty = endpoint disabled).
METRICS_TOKEN = env("DJANGO_METRICS_TOKEN", "")

ROOT_URLCONF = "config.urls"

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# --- Templates -----------------------------------------------------------
# Jinja2 is PRIMARY and listed first. Django templates are kept ONLY for the
# built-in admin at /sd/. See project.md section 24.

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.jinja2.Jinja2",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "environment": "config.jinja2.environment",
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "config.jinja2.csrf",
                "apps.core.context_processors.tenant",
                "apps.control.context_processors.control",
            ],
        },
    },
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# --- Database ----------------------------------------------------------
# SQLite in dev, PostgreSQL in prod (see production.py).

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Auth ------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "control:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"


# --- I18N / TZ -----------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# --- Proxy / real client IP -------------------------------------------
# When True, RealClientIPMiddleware rewrites REMOTE_ADDR from CF-Connecting-IP /
# X-Forwarded-For so throttling + audit logs see the real client. Only enable
# behind a proxy (Cloudflare) whose headers you trust and whose IPs are the
# only ones that can reach the origin.
TRUST_PROXY_HEADERS = env_bool("DJANGO_TRUST_PROXY_HEADERS", False)


# --- Celery ---------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", env("REDIS_URL", "redis://localhost:6379/0"))
CELERY_RESULT_BACKEND = None                 # fire-and-forget
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TIME_LIMIT = 120
CELERY_TASK_SOFT_TIME_LIMIT = 90
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BEAT_SCHEDULE = {
    "webhook-retries": {
        "task": "apps.webhooks.tasks.retry_due_task",
        "schedule": 300.0,   # every 5 min
    },
    "verify-pending-domains": {
        "task": "apps.projects.tasks.verify_pending_domains_task",
        "schedule": 300.0,   # every 5 min — auto-verify shortly after DNS is set
    },
    "billing-issue-due-invoices": {
        "task": "apps.billing.tasks.issue_due_invoices_task",
        "schedule": 3600.0 * 6,   # every 6 h
    },
    "billing-suspend-overdue": {
        "task": "apps.billing.tasks.suspend_overdue_task",
        "schedule": 3600.0 * 6,
    },
}


# --- Static / Media ----------------------------------------------------

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Let Django serve uploaded media itself. Off in real deploys (nginx / CDN owns
# /media/), but handy when hitting the app directly with no proxy in front.
# DEBUG implies it.
SERVE_MEDIA = env_bool("DJANGO_SERVE_MEDIA", False)

# Custom-domain verification (apps.projects.domains). PLATFORM_PUBLIC_IP is this
# server's public A-record target; a store owner who points their domain
# straight here (DNS-only) is verified without a TXT record. Cloudflare-proxied
# domains are verified via the /.well-known/sd-domain-check token instead.
PLATFORM_PUBLIC_IP = env("PLATFORM_PUBLIC_IP", "")

# Hosts that belong to the platform itself, not to any store. The root URL
# serves the marketing landing page on these, and ProjectResolverMiddleware
# never resolves them to a project even if a Domain row or primary_domain
# points at them. "www." is stripped before matching. Comma-separated.
def _bare_host(raw):
    h = (raw or "").strip().lower().split(":")[0].rstrip(".")
    return h[4:] if h.startswith("www.") else h


PLATFORM_HOSTS = [h for h in (_bare_host(x) for x in env_list("DJANGO_PLATFORM_HOSTS", [])) if h]

# Product image optimisation (apps.media.optimize + apps.catalog.tasks).
# Uploads are re-encoded to WebP in the background, squeezed under the target
# size, with responsive renditions generated alongside.
PRODUCT_IMAGE_TARGET_KB = int(env("PRODUCT_IMAGE_TARGET_KB", "200"))
PRODUCT_IMAGE_MAX_EDGE = int(env("PRODUCT_IMAGE_MAX_EDGE", "2048"))
PRODUCT_IMAGE_OPTIMIZE = env_bool("PRODUCT_IMAGE_OPTIMIZE", True)
# Uploads already at or under this and in a web format (JPEG/WebP, sane
# dimensions) are kept untouched — recorded, not re-encoded.
PRODUCT_IMAGE_SKIP_UNDER_KB = int(env("PRODUCT_IMAGE_SKIP_UNDER_KB", "300"))
# Celery rate cap for apps.catalog.tasks.optimize_product_image (read on the
# decorator). Keep low so a backfill batch stays a background trickle.
PRODUCT_IMAGE_RATE_LIMIT = env("PRODUCT_IMAGE_RATE_LIMIT", "6/m")


# --- DRF ---------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.api.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.URLPathVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
    "DEFAULT_FILTER_BACKENDS": [
        "rest_framework.filters.OrderingFilter",
        "rest_framework.filters.SearchFilter",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "240/min",
        "auth": "10/min",
        "checkout": "20/min",
        "write": "120/min",
    },
    "DEFAULT_SCHEMA_CLASS": "rest_framework.schemas.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.api.exceptions.api_exception_handler",
}
