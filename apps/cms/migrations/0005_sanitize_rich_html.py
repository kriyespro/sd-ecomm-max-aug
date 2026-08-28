"""One-off backfill: run existing rich-HTML fields through the sanitiser.

New writes are cleaned in each model's save(); this catches rows already in the
database. Not reversible (the pre-sanitise HTML is intentionally discarded).
"""

from django.db import migrations


def _sanitise(apps, schema_editor):
    from apps.core.html import sanitize_html

    targets = [
        ("cms", "Page", "body"),
        ("cms", "FAQ", "answer"),
        ("catalog", "Product", "description"),
    ]
    for app_label, model_name, field in targets:
        Model = apps.get_model(app_label, model_name)
        for pk, raw in Model.objects.values_list("pk", field):
            cleaned = sanitize_html(raw or "")
            if cleaned != (raw or ""):
                Model.objects.filter(pk=pk).update(**{field: cleaned})


class Migration(migrations.Migration):

    dependencies = [
        ("cms", "0004_skin_author_skin_is_sandboxed_skin_project_and_more"),
        ("catalog", "0003_product_rating_avg_product_rating_count"),
    ]

    operations = [
        migrations.RunPython(_sanitise, migrations.RunPython.noop),
    ]
