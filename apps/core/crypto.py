"""Field-level encryption for secrets stored at rest: payment provider
credentials, WhatsApp/social OAuth tokens, AI API keys, the platform's own
Razorpay/Meta secrets. Uses Fernet (symmetric, authenticated encryption,
from the `cryptography` package) keyed by ``settings.FIELD_ENCRYPTION_KEYS``.

``apps.payments.models.PaymentProviderConfig.credentials`` carried a comment
calling this out as a TODO since the field was first added -- it was never
actually wired up.

Key rotation: ``FIELD_ENCRYPTION_KEYS`` is an ordered list (newest first). A
new value always encrypts with the first key; ``MultiFernet`` tries every
key in the list on decrypt, so rotating a key is: prepend the new one,
deploy, re-save every row that matters (or wait for it to be re-saved
naturally), then drop the old key from the list.
"""

import json

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def _fernet():
    from cryptography.fernet import Fernet, MultiFernet

    keys = getattr(settings, "FIELD_ENCRYPTION_KEYS", None)
    if not keys:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEYS is empty -- set DJANGO_FIELD_ENCRYPTION_KEY "
            "(generate one with: python -c \"from cryptography.fernet import "
            "Fernet; print(Fernet.generate_key().decode())\")."
        )
    return MultiFernet([Fernet(k) for k in keys])


class EncryptedTextField(models.TextField):
    """A ``TextField`` whose value is Fernet-encrypted at rest. An empty
    value is stored as-is -- nothing to protect, and it keeps ``blank=True``
    fields' "" == "" ergonomics unchanged on both sides."""

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value:
            return value
        return _fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if not value:
            return value
        from cryptography.fernet import InvalidToken

        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            # A row saved before encryption was wired up, or before the
            # one-off backfill migration reached it. Hand back the raw
            # value instead of 500ing every page that reads this model --
            # it gets encrypted for real the next time the row is saved.
            return value


class EncryptedJSONField(models.JSONField):
    """A ``JSONField`` whose value is Fernet-encrypted at rest, stored as
    ``{"_enc": "<token>"}`` -- the column stays valid JSON/JSONB with no
    schema change, while the real contents are opaque to anyone reading the
    database directly."""

    def get_db_prep_value(self, value, connection, prepared=False):
        if not prepared:
            if value:
                token = _fernet().encrypt(
                    json.dumps(value, cls=self.encoder).encode()
                ).decode()
                value = {"_enc": token}
            value = self.get_prep_value(value)
        return connection.ops.adapt_json_value(value, self.encoder)

    def from_db_value(self, value, expression, connection):
        value = super().from_db_value(value, expression, connection)
        if not isinstance(value, dict) or "_enc" not in value:
            return value
        from cryptography.fernet import InvalidToken

        try:
            return json.loads(_fernet().decrypt(value["_enc"].encode()), cls=self.decoder)
        except InvalidToken:
            # Same pre-encryption fallback as EncryptedTextField -- this
            # branch means a row somehow stored a literal {"_enc": ...} dict
            # as its real plaintext credentials, which no provider does.
            return value
