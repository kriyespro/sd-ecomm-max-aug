from django.db import migrations
from django.utils import timezone


def backfill(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    Domain = apps.get_model("projects", "Domain")
    now = timezone.now()
    for project in Project.objects.exclude(primary_domain__isnull=True).exclude(primary_domain=""):
        host = project.primary_domain.strip().lower().rstrip(".")
        if not host:
            continue
        if Domain.objects.filter(host=host).exists():
            continue
        Domain.objects.filter(project=project, is_primary=True).update(is_primary=False)
        Domain.objects.create(
            project=project, host=host,
            is_verified=True, is_primary=True, verified_at=now,
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0009_project_brand'),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
