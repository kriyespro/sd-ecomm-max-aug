"""Periodic billing jobs (Celery beat)."""

from celery import shared_task


@shared_task(name="apps.billing.tasks.issue_due_invoices_task")
def issue_due_invoices_task():
    from .services import issue_due_invoices

    issue_due_invoices()


@shared_task(name="apps.billing.tasks.suspend_overdue_task")
def suspend_overdue_task():
    from .services import suspend_overdue

    suspend_overdue()
