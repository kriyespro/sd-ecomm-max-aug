# One-off backfill: meta_capi_token/server_token were plaintext before
# 0004_alter_platformtrackingsettings_meta_capi_token_and_more switched them
# to apps.core.crypto.EncryptedTextField. See apps/ai/migrations/
# 0003_reencrypt_api_key.py for why this re-save step is needed and safe to
# run more than once.

from django.db import migrations


def reencrypt(apps, schema_editor):
    PlatformTrackingSettings = apps.get_model("marketing", "PlatformTrackingSettings")
    for row in PlatformTrackingSettings.objects.all().iterator():
        row.save(update_fields=["meta_capi_token"])

    TrackingIntegration = apps.get_model("marketing", "TrackingIntegration")
    for row in TrackingIntegration.objects.all().iterator():
        row.save(update_fields=["server_token"])


class Migration(migrations.Migration):

    dependencies = [
        ("marketing", "0004_alter_platformtrackingsettings_meta_capi_token_and_more"),
    ]

    operations = [
        migrations.RunPython(reencrypt, migrations.RunPython.noop),
    ]
