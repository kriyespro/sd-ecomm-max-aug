"""Existing stores predate the onboarding wizard — mark them done so their
owners aren't bounced into it. New stores start un-onboarded."""

from django.db import migrations


def mark_onboarded(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    for p in Project.objects.all().iterator():
        ff = p.feature_flags or {}
        if not ff.get("onboarded"):
            ff["onboarded"] = True
            p.feature_flags = ff
            p.save(update_fields=["feature_flags"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0003_project_allowed_skins"),
    ]

    operations = [
        migrations.RunPython(mark_onboarded, noop),
    ]
