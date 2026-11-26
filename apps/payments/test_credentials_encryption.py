"""PaymentProviderConfig.credentials used to be a plain JSONField -- anyone
with DB access (a backup file, a read replica, a compromised reporting
account) could read a store's live Razorpay/Stripe key_secret in the clear.
Now apps.core.crypto.EncryptedJSONField. The ORM round-trip is already
covered by the rest of this app's suite exercising PaymentProviderConfig --
this file checks the one thing that matters: the raw bytes actually on
disk are opaque."""

from django.db import connection
from django.test import TestCase

from apps.payments.models import PaymentProviderConfig, Provider
from apps.projects.models import Project


class CredentialsEncryptedAtRestTests(TestCase):
    def test_key_secret_not_readable_in_the_raw_column(self):
        project = Project.objects.create(name="EncCo", status="active", currency="INR")
        cfg = PaymentProviderConfig.objects.create(
            project=project, provider=Provider.RAZORPAY,
            credentials={"key_id": "rzp_live_abc123", "key_secret": "super-secret-value"},
        )

        with connection.cursor() as cur:
            cur.execute(
                "SELECT credentials FROM payments_paymentproviderconfig WHERE id = %s",
                [cfg.pk],
            )
            raw = cur.fetchone()[0]

        self.assertNotIn("super-secret-value", raw)
        self.assertNotIn("key_secret", raw)
        self.assertIn("_enc", raw)

        cfg.refresh_from_db()
        self.assertEqual(cfg.credentials["key_secret"], "super-secret-value")
