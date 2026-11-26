# One-off backfill: credentials was a plain JSONField before
# 0003_alter_courierconfig_credentials switched it to
# apps.core.crypto.EncryptedJSONField. See apps/ai/migrations/
# 0003_reencrypt_api_key.py for why this re-save step is needed and safe to
# run more than once.

from django.db import migrations


def reencrypt(apps, schema_editor):
    CourierConfig = apps.get_model("shipping", "CourierConfig")
    for row in CourierConfig.objects.all().iterator():
        row.save(update_fields=["credentials"])


class Migration(migrations.Migration):

    dependencies = [
        ("shipping", "0003_alter_courierconfig_credentials"),
    ]

    operations = [
        migrations.RunPython(reencrypt, migrations.RunPython.noop),
    ]
