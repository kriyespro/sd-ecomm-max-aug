"""Keep the OpenRouter free-model catalog warm. A cold cache means a
merchant's "Generate" click pays the catalog-fetch latency inline, stacking
on top of the request's own tight time budget (see apps.ai.services) — this
periodic refresh keeps that a rare, not routine, occurrence."""

from celery import shared_task

from . import openrouter


@shared_task(name="apps.ai.tasks.refresh_free_models_task")
def refresh_free_models_task():
    models = openrouter.refresh_free_models_cache()
    return len(models)
