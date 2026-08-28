"""Development settings. Default for local work."""

from .base import *  # noqa: F401,F403
from .base import env_bool, env_list

DEBUG = env_bool("DJANGO_DEBUG", True)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["*"])

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Run Celery tasks inline — no broker or worker needed for local work.
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", True)
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "memory://")

# Allow any Host to resolve to a project during local multi-domain testing.
CORS_ALLOW_ALL_ORIGINS = True
