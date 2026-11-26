# One-off backfill: api_key was plaintext before 0002_alter_aiproviderkey_api_key
# switched it to apps.core.crypto.EncryptedTextField. The field class change
# alone doesn't touch bytes already on disk -- re-saving every row here runs
# each value through the new field's encrypt-on-save path. Safe to run more
# than once: an already-encrypted value just gets re-encrypted (a fresh
# Fernet token for the same plaintext, not a correctness issue).

from django.db import migrations


def reencrypt(apps, schema_editor):
    AiProviderKey = apps.get_model("ai", "AiProviderKey")
    for row in AiProviderKey.objects.all().iterator():
        row.save(update_fields=["api_key"])


class Migration(migrations.Migration):

    dependencies = [
        ("ai", "0002_alter_aiproviderkey_api_key"),
    ]

    operations = [
        migrations.RunPython(reencrypt, migrations.RunPython.noop),
    ]
