"""Platform branding is off on every plan now — drop it as a per-plan toggle
and remove the branding bullets from the feature lists."""

from django.db import migrations

_STRIP = {"Platform branding", "Remove platform branding"}


def apply(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for plan in Plan.objects.all():
        plan.remove_platform_branding = True
        if plan.features:
            plan.features = [f for f in plan.features if f not in _STRIP]
        plan.save(update_fields=["remove_platform_branding", "features"])


def revert(apps, schema_editor):
    # one-way — the copy change isn't worth reconstructing
    pass


class Migration(migrations.Migration):
    dependencies = [("billing", "0005_three_plan_lineup")]
    operations = [migrations.RunPython(apply, revert)]
