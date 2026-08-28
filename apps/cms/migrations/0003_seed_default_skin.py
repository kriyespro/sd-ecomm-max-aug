from django.db import migrations


def create_default_skin(apps, schema_editor):
    Skin = apps.get_model("cms", "Skin")
    Skin.objects.update_or_create(
        slug="default",
        defaults={
            "label": "Default",
            "description": (
                "The built-in storefront — server-rendered Jinja + HTMX + "
                "Tailwind. Every store falls back to this."
            ),
            "is_active": True,
            "is_default": True,
        },
    )


def remove_default_skin(apps, schema_editor):
    Skin = apps.get_model("cms", "Skin")
    Skin.objects.filter(slug="default").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("cms", "0002_skin_themesettings_skin"),
    ]

    operations = [
        migrations.RunPython(create_default_skin, remove_default_skin),
    ]
