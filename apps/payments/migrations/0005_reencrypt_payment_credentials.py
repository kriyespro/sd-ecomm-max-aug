# One-off backfill: credentials was a plain JSONField before
# 0004_alter_paymentproviderconfig_credentials switched it to
# apps.core.crypto.EncryptedJSONField. See apps/ai/migrations/
# 0003_reencrypt_api_key.py for why this re-save step is needed and safe to
# run more than once.

from django.db import migrations


def reencrypt(apps, schema_editor):
    PaymentProviderConfig = apps.get_model("payments", "PaymentProviderConfig")
    for row in PaymentProviderConfig.objects.all().iterator():
        row.save(update_fields=["credentials"])


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0004_alter_paymentproviderconfig_credentials"),
    ]

    operations = [
        migrations.RunPython(reencrypt, migrations.RunPython.noop),
    ]
