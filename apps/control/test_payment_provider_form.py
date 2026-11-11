"""PaymentProviderForm: the new cod_advance_amount field needs an enabled
online gateway (UPI/Razorpay) on the same project before it can be set."""

from decimal import Decimal

from django.test import TestCase

from apps.control.forms import PaymentProviderForm
from apps.payments.models import PaymentProviderConfig, Provider
from apps.projects.models import Project


class CodAdvanceAmountValidationTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="FormCo", status="active", currency="INR")

    def _data(self, **overrides):
        data = {
            "provider": Provider.COD, "display_name": "", "is_enabled": "on",
            "is_test_mode": "on", "priority": "100", "cod_advance_amount": "0",
            "key_id": "", "key_secret": "", "upi_id": "", "payee_name": "",
        }
        data.update(overrides)
        return data

    def test_advance_without_any_enabled_gateway_is_rejected(self):
        form = PaymentProviderForm(data=self._data(cod_advance_amount="50"), project=self.project)
        self.assertFalse(form.is_valid())
        self.assertIn("cod_advance_amount", form.errors)

    def test_advance_with_upi_enabled_is_accepted(self):
        PaymentProviderConfig.objects.create(
            project=self.project, provider=Provider.UPI, is_enabled=True,
            credentials={"upi_id": "shop@bank"},
        )
        form = PaymentProviderForm(data=self._data(cod_advance_amount="50"), project=self.project)
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save()
        self.assertEqual(obj.cod_advance_amount, Decimal("50"))

    def test_zero_advance_needs_no_gateway(self):
        form = PaymentProviderForm(data=self._data(cod_advance_amount="0"), project=self.project)
        self.assertTrue(form.is_valid(), form.errors)
