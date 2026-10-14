"""Register the built-in "Zyvira" skin.

A fine-jewellery storefront skin — deep wine (#570517) + warm gold (#cea452)
on cream/white, DM Serif Display over Kumbh Sans, pill buttons and 10px-rounded
photo cards. Templates ship under ``templates/shopfront/skins/zyvira/``; every
other page inherits from ``skins/default/`` via the skin-aware loader
fallback.
"""

from django.db import migrations

SLUG = "zyvira"
LABEL = "Zyvira"
DESCRIPTION = (
    "Fine fashion jewellery — deep wine and warm gold on cream, DM Serif "
    "Display over Kumbh Sans, pill buttons and rounded photo cards."
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
        ("cms", "0033_storeprofile_whatsapp_order"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
