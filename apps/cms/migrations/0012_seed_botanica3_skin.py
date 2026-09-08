"""Register the built-in "Botanica 3.0" skin.

A copy of ``botanica2`` with a squared-off "Shop by category" row (up to 8
tiles), a new "Shop by budget" band section, and an Instagram feed section.
Templates ship under ``templates/shopfront/skins/botanica3/``; every other page
inherits from ``skins/default/`` via the skin-aware loader fallback.
``botanica2`` is left untouched.
"""

from django.db import migrations

SLUG = "botanica3"
LABEL = "Botanica 3.0"
DESCRIPTION = (
    "Botanica 2.0 refreshed — an 8-up squared category row, a Shop-by-budget "
    "band section, and an Instagram feed. Fraunces over Outfit."
)


def seed(apps, schema_editor):
    Skin = apps.get_model("cms", "Skin")
    Skin.objects.update_or_create(
        slug=SLUG,
        defaults={
            "label": LABEL,
            "description": DESCRIPTION,
            "source": "builtin",
            "is_sandboxed": False,
            "status": "approved",
            "is_active": True,
            "is_default": False,
            "project": None,
        },
    )


def unseed(apps, schema_editor):
    Skin = apps.get_model("cms", "Skin")
    Skin.objects.filter(slug=SLUG, source="builtin").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("cms", "0011_budgetband_instagramitem"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
