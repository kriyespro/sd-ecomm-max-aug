# One-off backfill: razorpay_key_secret/razorpay_webhook_secret were
# plaintext before 0017_alter_billingsettings_razorpay_key_secret_and_more
# switched them to apps.core.crypto.EncryptedTextField. See apps/ai/
# migrations/0003_reencrypt_api_key.py for why this re-save step is needed
# and safe to run more than once.

from django.db import migrations


def reencrypt(apps, schema_editor):
    BillingSettings = apps.get_model("billing", "BillingSettings")
    for row in BillingSettings.objects.all().iterator():
        row.save(update_fields=["razorpay_key_secret", "razorpay_webhook_secret"])


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0017_alter_billingsettings_razorpay_key_secret_and_more"),
    ]

    operations = [
        migrations.RunPython(reencrypt, migrations.RunPython.noop),
    ]
