"""Production settings. Requires real env vars — no insecure defaults."""

from .base import *  # noqa: F401,F403
from .base import MIDDLEWARE, env, env_bool, env_list

DEBUG = False

SECRET_KEY = env("DJANGO_SECRET_KEY")  # must be set

# Multi-tenant: stores connect their own verified custom domains, so a static
# host allowlist can't cover them. Default to "*" and rely on the edge proxy
# (Caddy / nginx / Traefik) to only forward known hosts. Lock down with
# DJANGO_ALLOWED_HOSTS if you terminate TLS for a fixed set of domains.
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["*"])

# --- Database --------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(env("DJANGO_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
    }
}

# --- Cache + sessions ----------------------------------------------
# Redis backs the cache and, through it, DRF throttle counters (shared across
# gunicorn workers). Sessions read from Redis, write through to the DB.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", "redis://localhost:6379/1"),
        "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
    }
}
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

# --- Static + media ----------------------------------------------
# WhiteNoise serves /static/ straight from the app process (hashed + compressed).
# Media (uploads) must live on a persistent volume or object store — never the
# container filesystem. Set DJANGO_MEDIA_URL to your CDN / bucket public base.
MIDDLEWARE = (
    MIDDLEWARE[:1] + ["whitenoise.middleware.WhiteNoiseMiddleware"] + MIDDLEWARE[1:]
)

STORAGES = {
    "default": {
        "BACKEND": env(
            "DJANGO_DEFAULT_FILE_STORAGE",
            "django.core.files.storage.FileSystemStorage",
        ),
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
MEDIA_URL = env("DJANGO_MEDIA_URL", "/media/")
WHITENOISE_MAX_AGE = 60 * 60 * 24 * 365

# --- Security hardening ------------------------------------------
# One switch for the whole HTTPS posture. Set DJANGO_HTTPS=false ONLY for a
# short bring-up over plain HTTP on the raw IP — flip it back to true the moment
# a cert is in place (cookies won't cross plain HTTP while it's true, so admin
# login 403s with a CSRF error).
HTTPS = env_bool("DJANGO_HTTPS", True)

SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", HTTPS)
SECURE_HSTS_SECONDS = 31536000 if HTTPS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = HTTPS
SECURE_HSTS_PRELOAD = HTTPS
# The proxy's proto header is trusted by default (TLS terminates at the edge).
# Set DJANGO_TRUST_PROXY_PROTO=false when the origin is reachable directly on
# plain HTTP — otherwise a client can forge X-Forwarded-Proto: https and defeat
# SECURE_SSL_REDIRECT / secure-cookie enforcement. Pair "false" with a firewall
# that only admits the proxy.
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https")
    if env_bool("DJANGO_TRUST_PROXY_PROTO", True)
    else None
)
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = HTTPS
CSRF_COOKIE_SECURE = HTTPS
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", [])

# --- Email --------------------------------------------------------
EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", "no-reply@example.com")

# --- Logging — everything to stdout for the container runtime -----
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "shopfront.skin": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
