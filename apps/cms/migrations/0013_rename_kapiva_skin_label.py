"""Rename the ``kapiva`` skin's label "Botanica" -> "Kapiva".

Migration 0007 seeded it as "Botanica"; with ``botanica2`` ("Botanica 2.0") and
``botanica3`` ("Botanica 3.0") now beside it in every skin picker, the bare
"Botanica" looked like the kapiva skin had gone missing. Only the display label
changes — the slug and templates are untouched.
"""

from django.db import migrations


def rename(apps, schema_editor):
    Skin = apps.get_model("cms", "Skin")
    Skin.objects.filter(slug="kapiva", label="Botanica").update(label="Kapiva")


def unrename(apps, schema_editor):
    Skin = apps.get_model("cms", "Skin")
    Skin.objects.filter(slug="kapiva", label="Kapiva").update(label="Botanica")


class Migration(migrations.Migration):

    dependencies = [
        ("cms", "0012_seed_botanica3_skin"),
    ]

    operations = [
        migrations.RunPython(rename, unrename),
    ]
