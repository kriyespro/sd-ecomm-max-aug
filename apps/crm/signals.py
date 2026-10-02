"""Auto-log subscription money into the CRM ledger.

Wrapped so a CRM hiccup can never break invoice payment."""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.billing.models import Invoice, InvoiceStatus

log = logging.getLogger(__name__)


@receiver(post_save, sender=Invoice, dispatch_uid="crm_invoice_paid_collection")
def invoice_paid_to_collection(sender, instance, **kwargs):
    if instance.status != InvoiceStatus.PAID:
        return
    try:
        from .services import record_invoice_collection

        record_invoice_collection(instance)
    except Exception:  # noqa: BLE001 — reporting must never block payment
        log.exception("crm: could not log collection for invoice %s", instance.pk)
