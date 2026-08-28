"""Celery application. Tasks live in each app's ``tasks.py`` (autodiscovered).

Dev runs with ``CELERY_TASK_ALWAYS_EAGER`` (inline, no broker). Prod runs a
``worker`` and a ``beat`` container against Redis.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

app = Celery("sd")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
