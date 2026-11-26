# One-off backfill: access_token/app_secret were plaintext before
# 0003_alter_whatsappaccount_access_token_and_more switched them to
# apps.core.crypto.EncryptedTextField. See apps/ai/migrations/
# 0003_reencrypt_api_key.py for why this re-save step is needed and safe to
# run more than once.

from django.db import migrations


def reencrypt(apps, schema_editor):
    WhatsAppAccount = apps.get_model("whatsapp", "WhatsAppAccount")
    for row in WhatsAppAccount.objects.all().iterator():
        row.save(update_fields=["access_token", "app_secret"])


class Migration(migrations.Migration):

    dependencies = [
        ("whatsapp", "0003_alter_whatsappaccount_access_token_and_more"),
    ]

    operations = [
        migrations.RunPython(reencrypt, migrations.RunPython.noop),
    ]
