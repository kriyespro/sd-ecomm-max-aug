"""Register the built-in "Botanica 2.0" skin.

A hand-built upgrade of ``kapiva`` ("Botanica"): its own ``base.jinja``,
``home.jinja``, ``shop.jinja``, ``partials/_card.jinja`` and
``partials/_grid.jinja`` ship in the repo under
``templates/shopfront/skins/botanica2/``; every other page template inherits
from ``skins/default/`` via the skin-aware loader fallback.

Only the ``Skin`` row is seeded here. ``manage.py seed_botanica2_skin``
re-upserts it and can point a store's ThemeSettings at it.
"""

from django.db import migrations

SLUG = "botanica2"
LABEL = "Botanica 2.0"
DESCRIPTION = (
    "Refined wellness & Ayurveda — warm cream, deep botanical green, gold "
    "hairlines, soft rounded cards. Fraunces over Outfit."
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
        ("cms", "0007_seed_builtin_skins"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
