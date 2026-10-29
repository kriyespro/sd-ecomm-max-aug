"""Register the built-in "KAANIDHI2.0" skin.

A fine fashion-jewellery storefront skin built fully from scratch — not a
thin override of ``skins/default/`` — deep maroon (#501116) on warm cream,
a single geometric sans (Jost) for body and headings with a Parisienne
script wordmark, circular category tiles, a dark bestseller/trust/footer
block. Ships every required page and partial itself under
``templates/shopfront/skins/kaanidhi2-0/`` — nothing falls back to another
skin's templates.
"""

from django.db import migrations

SLUG = "kaanidhi2-0"
LABEL = "KAANIDHI2.0"
DESCRIPTION = (
    "Fine fashion jewellery, built fresh from the ground up — deep maroon "
    "on warm cream, a single Jost sans with a script wordmark, circular "
    "category tiles and a dark bestseller/footer block. Ships every page "
    "itself."
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
        ("cms", "0036_seed_vireej2_skin"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
