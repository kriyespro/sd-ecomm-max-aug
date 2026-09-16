"""Development settings. Default for local work."""

import sys

from .base import *  # noqa: F401,F403
from .base import env_bool, env_list

DEBUG = env_bool("DJANGO_DEBUG", True)

# manage.py test runs against this settings module (no dedicated test.py).
# The mandatory-2FA gate is exercised directly and deliberately in
# apps.accounts.test_two_factor (it re-adds this middleware via
# override_settings) — leaving it in for the whole suite would force every
# unrelated test's superuser/platform-admin fixture to also carry a
# confirmed TOTP secret just to reach an /admin/ page, for no extra coverage.
if "test" in sys.argv:
    MIDDLEWARE = [m for m in MIDDLEWARE if m != "apps.accounts.middleware.TwoFactorEnforcementMiddleware"]

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["*"])

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Run Celery tasks inline — no broker or worker needed for local work.
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", True)
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "memory://")

# Allow any Host to resolve to a project during local multi-domain testing.
CORS_ALLOW_ALL_ORIGINS = True
