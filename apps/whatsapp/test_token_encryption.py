"""WhatsAppAccount.access_token/app_secret used to be plain CharFields --
anyone with DB access could read a store's live Meta access token in the
clear. Now apps.core.crypto.EncryptedTextField. Checks the one thing the
rest of this app's ORM-level test suite can't: the raw bytes on disk are
opaque, not just that the model attribute round-trips."""

from django.db import connection
from django.test import TestCase

from apps.projects.models import Project
from apps.whatsapp.models import WhatsAppAccount


class TokenEncryptedAtRestTests(TestCase):
    def test_access_token_not_readable_in_the_raw_column(self):
        project = Project.objects.create(name="WaEncCo", status="active", currency="INR")
        account = WhatsAppAccount.objects.create(
            project=project, phone_number_id="123", access_token="EAAG-super-secret-token",
            app_secret="also-secret",
        )

        with connection.cursor() as cur:
            cur.execute(
                "SELECT access_token, app_secret FROM whatsapp_whatsappaccount WHERE id = %s",
                [account.pk],
            )
            token_raw, secret_raw = cur.fetchone()

        self.assertNotIn("EAAG-super-secret-token", token_raw)
        self.assertNotIn("also-secret", secret_raw)

        account.refresh_from_db()
        self.assertEqual(account.access_token, "EAAG-super-secret-token")
        self.assertEqual(account.app_secret, "also-secret")
