"""apps.core.crypto: Fernet field-level encryption for secrets at rest.

Unit-tests the field classes directly (get_prep_value/get_db_prep_value/
from_db_value) rather than through a throwaway model -- the real DB
round-trip for each field is covered by the model it's actually used on
(apps.payments/whatsapp/social/ai/billing/marketing test suites)."""

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from apps.core.crypto import EncryptedJSONField, EncryptedTextField

# Two independently-generated dev-only Fernet keys, for rotation tests.
_KEY_A = "zbyOySTAIbdwn47NHHXIUx__ebkwwihweo_h1LMphYk="
_KEY_B = "gkcfobP0xy2yOlLXcjc1494B3fjP1CqzGqnkNHwXs5o="


class EncryptedTextFieldTests(SimpleTestCase):
    def setUp(self):
        self.field = EncryptedTextField()

    def test_round_trips_and_is_opaque_at_rest(self):
        stored = self.field.get_prep_value("super-secret-value")
        self.assertNotIn("super-secret-value", stored)
        self.assertEqual(self.field.from_db_value(stored, None, None), "super-secret-value")

    def test_blank_passes_through_unencrypted(self):
        self.assertEqual(self.field.get_prep_value(""), "")
        self.assertEqual(self.field.from_db_value("", None, None), "")
        self.assertIsNone(self.field.from_db_value(None, None, None))

    @override_settings(FIELD_ENCRYPTION_KEYS=[])
    def test_missing_key_raises_clearly(self):
        with self.assertRaises(ImproperlyConfigured):
            self.field.get_prep_value("x")

    def test_legacy_plaintext_value_reads_back_unencrypted(self):
        """A row written before encryption was wired up (or before the
        backfill migration reached it) must not 500 the page that reads it."""
        self.assertEqual(self.field.from_db_value("plain-legacy-value", None, None), "plain-legacy-value")


class EncryptedJSONFieldTests(SimpleTestCase):
    def setUp(self):
        self.field = EncryptedJSONField()

    def _round_trip(self, value):
        stored = self.field.get_db_prep_value(value, connection=_FakeConnection(), prepared=False)
        return self.field.from_db_value(stored, None, _FakeConnection())

    def test_round_trips_and_is_opaque_at_rest(self):
        value = {"key_id": "abc", "key_secret": "shh"}
        stored = self.field.get_db_prep_value(value, connection=_FakeConnection(), prepared=False)
        self.assertNotIn("key_secret", stored)
        self.assertNotIn("shh", stored)
        self.assertEqual(self._round_trip(value), value)

    def test_empty_dict_stays_empty(self):
        self.assertEqual(self._round_trip({}), {})

    def test_legacy_plaintext_json_reads_back_unencrypted(self):
        import json

        legacy = json.dumps({"key_id": "legacy"})
        self.assertEqual(self.field.from_db_value(legacy, None, _FakeConnection()), {"key_id": "legacy"})


@override_settings(FIELD_ENCRYPTION_KEYS=[_KEY_B, _KEY_A])
class KeyRotationTests(SimpleTestCase):
    def test_old_key_still_decrypts_after_rotation(self):
        field = EncryptedTextField()
        with override_settings(FIELD_ENCRYPTION_KEYS=[_KEY_A]):
            stored = field.get_prep_value("rotate-me")
        # FIELD_ENCRYPTION_KEYS is back to [_KEY_B, _KEY_A] here -- decrypt
        # must still succeed by falling through to the old key.
        self.assertEqual(field.from_db_value(stored, None, None), "rotate-me")


class _FakeConnection:
    """adapt_json_value() on sqlite/postgres just needs .ops.adapt_json_value
    -- stand in with the plain json.dumps a real connection falls back to."""

    class ops:
        @staticmethod
        def adapt_json_value(value, encoder):
            import json

            return json.dumps(value, cls=encoder)
