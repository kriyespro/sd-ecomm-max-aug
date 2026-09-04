"""Checkout payment-method gating (a shopper must not settle their own order)."""

from django.test import TestCase

from apps.payments.models import PaymentProviderConfig, Provider
from apps.payments.providers.manual import ManualProvider
from apps.projects.models import Project

from .services import CheckoutError, _validate_payment_method


class PaymentMethodGateTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Acme")

    def _enable(self, provider):
        PaymentProviderConfig.objects.create(
            project=self.project, provider=provider, is_enabled=True
        )

    def test_blank_method_is_allowed(self):
        _validate_payment_method(self.project, "")  # no raise

    def test_manual_is_never_selectable_even_when_enabled(self):
        self._enable(Provider.MANUAL)
        with self.assertRaises(CheckoutError):
            _validate_payment_method(self.project, "manual")

    def test_method_not_enabled_is_rejected(self):
        with self.assertRaises(CheckoutError):
            _validate_payment_method(self.project, "razorpay")

    def test_enabled_gateway_passes(self):
        self._enable(Provider.RAZORPAY)
        _validate_payment_method(self.project, "razorpay")  # no raise

    def test_manual_provider_never_self_verifies(self):
        self.assertFalse(ManualProvider(config=None).verify(payment=None, data={}))
