"""Register the built-in "VIREEJ2.0" skin.

A fine fashion-jewellery storefront skin built fully from scratch — not a
thin override of ``skins/default/`` — deep wine (#570517) + warm gold
(#cea452) on cream/white, DM Serif Display over Kumbh Sans, logo-left
single-row header, rounded-2xl cards, pill buttons. Ships every required
page and partial itself under ``templates/shopfront/skins/vireej2-0/`` —
nothing falls back to another skin's templates.
"""

from django.db import migrations

SLUG = "vireej2-0"
LABEL = "VIREEJ2.0"
DESCRIPTION = (
    "Fine fashion jewellery, built fresh from the ground up — deep wine and "
    "warm gold on cream, DM Serif Display over Kumbh Sans, a logo-left "
    "header and rounded photo cards. Ships every page itself."
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
        ("cms", "0035_catalog_mode"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
