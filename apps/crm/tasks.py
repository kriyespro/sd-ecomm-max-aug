"""Celery beat entry points for the CRM's automatic jobs (see automation.py)."""

from celery import shared_task


@shared_task(name="apps.crm.tasks.trial_rescue_task")
def trial_rescue_task():
    from .automation import trial_rescue
    return trial_rescue()


@shared_task(name="apps.crm.tasks.store_health_task")
def store_health_task():
    from .automation import store_health
    return store_health()


@shared_task(name="apps.crm.tasks.recycle_leads_task")
def recycle_leads_task():
    from .automation import recycle_leads
    return recycle_leads()


@shared_task(name="apps.crm.tasks.admin_alerts_task")
def admin_alerts_task():
    """Hourly: both alerts gate themselves on hour / weekday / already-sent."""
    from .automation import quiet_alert, weekly_digest
    return {"quiet": quiet_alert(), "digest": weekly_digest()}
