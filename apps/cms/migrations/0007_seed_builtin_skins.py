"""Register the 14 built-in skins so every store can pick them on the Theme
screen without an operator running ``manage.py seed_skins`` / ``seed_ornza_skin``
by hand on each deploy.

Only the ``Skin`` rows are seeded here — the template folders
(``templates/shopfront/skins/<slug>/``) already ship in the repo. Re-running
``seed_skins`` still refreshes labels / descriptions and regenerates the
per-skin ``base.jinja`` from ``default/``.
"""

from django.db import migrations

# slug, label, description  (kept in step with seed_skins.PRESETS + seed_ornza_skin)
BUILTIN_SKINS = [
    ("mono", "Monochrome", "Stark black-and-white editorial. Big Archivo type, hairline rules."),
    ("noir", "Noir", "Dark luxe. Deep charcoal ground, gold accent, Playfair headlines."),
    ("bloom", "Bloom", "Soft and feminine. Blush paper, rose accent, Fraunces display."),
    ("grove", "Grove", "Earthy and organic. Warm oat tones, sage accent, Spectral serif."),
    ("cobalt", "Cobalt", "Bold and modern. Cool greys, electric blue, Space Grotesk."),
    ("sunbaked", "Sunbaked", "Warm terracotta. Marcellus + Work Sans, copper accent."),
    ("marble", "Marble", "Cool premium neutral. Cormorant + Manrope, stone accent."),
    ("neon", "Neon", "High-energy streetwear. Near-black ground, lime accent, heavy Archivo."),
    ("linen", "Linen", "Minimal Scandinavian. Off-white, taupe accent, EB Garamond."),
    ("coral", "Coral", "Vibrant playful DTC. Cream paper, coral accent, all-Poppins."),
    ("impact", "Impact", "Elegant jewellery — deep maroon & gold, Cormorant Garamond over Lato."),
    ("impact2", "Impact Bold", "Loud audio/streetwear — heavy Barlow, grey ground, violet pop, pill buttons."),
    ("kapiva", "Botanica", "Wellness & Ayurveda — cream + forest green + gold, Fraunces over Outfit."),
    ("ornza", "Ornza", "Champagne-gold luxe jewellery — Playfair Display + Poppins, ivory & espresso."),
]


def seed(apps, schema_editor):
    Skin = apps.get_model("cms", "Skin")
    for slug, label, description in BUILTIN_SKINS:
        Skin.objects.update_or_create(
            slug=slug,
            defaults={
                "label": label,
                "description": description,
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
    Skin.objects.filter(
        slug__in=[s[0] for s in BUILTIN_SKINS], source="builtin"
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("cms", "0006_storeprofile"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
