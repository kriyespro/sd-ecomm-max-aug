"""allow_full_backup on: growth, pro (the two plans this was actually asked
for), plus advanced/enterprise for consistency with them being higher tiers
still (retired/private, but a real subscriber on either shouldn't lose
something growth/pro has). basic and starter stay off -- catalog/CMS/theme
manual backup only, same as before this feature existed."""

from django.db import migrations

ON = ["growth", "pro", "advanced", "enterprise"]


def apply(apps, schema_editor):
    apps.get_model("billing", "Plan").objects.filter(code__in=ON).update(allow_full_backup=True)


def revert(apps, schema_editor):
    apps.get_model("billing", "Plan").objects.filter(code__in=ON).update(allow_full_backup=False)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0015_plan_allow_full_backup"),
    ]

    operations = [migrations.RunPython(apply, revert)]
