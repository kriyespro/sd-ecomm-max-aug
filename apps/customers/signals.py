"""Keep Customer stats fresh when orders change.

Orders never import customers; this listener is the one-way link. It only does
work when a revenue-relevant field actually changed and runs the aggregation
after the transaction commits, off the request's critical path.
"""

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

_RELEVANT = {"status", "payment_status", "grand_total", "placed_at", "customer", "customer_id"}


def _resync(project_id, email):
    from .models import Customer
    from .services import sync_customer_stats

    customer = Customer.objects.filter(project_id=project_id, email=email).first()
    if customer is not None:
        sync_customer_stats(customer)


@receiver(post_save, sender="orders.Order")
def _resync_customer_on_order(sender, instance, created, update_fields=None, **kwargs):
    if not created and update_fields is not None and not (_RELEVANT & set(update_fields)):
        return
    project_id, email = instance.project_id, instance.email
    transaction.on_commit(lambda: _resync(project_id, email))
