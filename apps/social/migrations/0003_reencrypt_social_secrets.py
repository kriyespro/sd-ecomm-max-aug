# One-off backfill: access_token/refresh_token/client_secret were plaintext
# before 0002_alter_socialaccount_access_token_and_more switched them to
# apps.core.crypto.EncryptedTextField. See apps/ai/migrations/
# 0003_reencrypt_api_key.py for why this re-save step is needed and safe to
# run more than once.

from django.db import migrations


def reencrypt(apps, schema_editor):
    SocialAccount = apps.get_model("social", "SocialAccount")
    for row in SocialAccount.objects.all().iterator():
        row.save(update_fields=["access_token", "refresh_token", "client_secret"])


class Migration(migrations.Migration):

    dependencies = [
        ("social", "0002_alter_socialaccount_access_token_and_more"),
    ]

    operations = [
        migrations.RunPython(reencrypt, migrations.RunPython.noop),
    ]
