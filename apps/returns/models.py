"""Self-serve return/exchange requests on a delivered order. One request per
order (``OneToOneField``) -- same simplicity trade-off as
``apps.referrals.ReferralCommission``/``apps.credits.LoyaltyEarning``; a
rejected request cannot be resubmitted in this pass.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TenantScopedModel

MONEY = dict(max_digits=12, decimal_places=2, null=True, blank=True,
             validators=[MinValueValidator(Decimal("0"))])


class RequestStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class RequestKind(models.TextChoices):
    RETURN = "return", "Return"
    EXCHANGE = "exchange", "Exchange"


class RefundMethod(models.TextChoices):
    STORE_CREDIT = "store_credit", "Store credit"
    GATEWAY_REFUND = "gateway_refund", "Gateway refund"
    MANUAL = "manual", "Manual (handled off-platform)"


class ReturnRequest(TenantScopedModel):
    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="return_request",
    )
    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="return_requests",
    )
    kind = models.CharField(max_length=10, choices=RequestKind.choices, default=RequestKind.RETURN)
    reason = models.CharField(max_length=255)
    note = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=RequestStatus.choices, default=RequestStatus.REQUESTED)

    resolved_refund_method = models.CharField(max_length=20, choices=RefundMethod.choices, blank=True)
    resolved_amount = models.DecimalField(**MONEY)
    resolved_note = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="resolved_return_requests",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"ReturnRequest<order={self.order_id}> {self.status}"
