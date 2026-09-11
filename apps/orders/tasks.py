"""Periodic order jobs (Celery beat)."""

from celery import shared_task


@shared_task(name="apps.orders.tasks.expire_stale_pending_orders_task")
def expire_stale_pending_orders_task():
    from .services import expire_stale_pending_orders

    expire_stale_pending_orders()
