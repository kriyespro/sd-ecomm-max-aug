"""Give every pre-existing store a trial subscription on the entry plan."""

from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def backfill(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    Plan = apps.get_model("billing", "Plan")
    Subscription = apps.get_model("billing", "Subscription")
    BillingSettings = apps.get_model("billing", "BillingSettings")

    plan = Plan.objects.filter(is_active=True, is_public=True).order_by("sort_order").first()
    if plan is None:
        return
    cfg, _ = BillingSettings.objects.get_or_create(pk=1)
    now = timezone.now()
    trial_end = now + timedelta(days=cfg.trial_days)

    for project in Project.objects.exclude(subscription__isnull=False):
        Subscription.objects.create(
            project=project, plan=plan, period="monthly", status="trialing",
            current_period_start=now, current_period_end=trial_end, trial_end=trial_end,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0002_seed_plans"),
        ("projects", "0003_project_allowed_skins"),
    ]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
