"""Seed the MVP catalogue of built-in storefront skins.

Each skin is a thin override of ``skins/default/``: it ships only its own
``base.jinja`` (palette + fonts + shell identity) and inherits every page
template from the default skin via the skin-aware loader fallback
(skin -> default -> bare).

    python manage.py seed_skins                       # (re)generate all 10
    python manage.py seed_skins --activate cobalt --project acme-store

Idempotent — safe to re-run. Does not touch the ``default`` skin, uploaded
skins, or any store's chosen skin (unless --activate is passed).
"""

import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.cms.models import Skin, SkinSource, SkinStatus
from apps.projects.models import Project

SKINS_DIR = Path(settings.BASE_DIR) / "templates" / "shopfront" / "skins"

# slug, label, description, google-fonts family query, display stack, sans stack,
# palette {paper, panel, ink, mute, line, accent, accent_soft}
PRESETS = [
    ("mono", "Monochrome", "Stark black-and-white editorial. Big Archivo type, hairline rules.",
     "Archivo:wght@400;500;600;800;900",
     '\'"Archivo"\', \'"Helvetica Neue"\', \'sans-serif\'',
     '\'"Archivo"\', \'system-ui\', \'sans-serif\'',
     dict(paper="#ffffff", panel="#fafafa", ink="#0a0a0a", mute="#6b6b6b",
          line="#e5e5e5", accent="#0a0a0a", accent_soft="#f0f0f0")),

    ("noir", "Noir", "Dark luxe. Deep charcoal ground, gold accent, Playfair headlines.",
     "Playfair+Display:wght@500;600;700&family=Inter:wght@300;400;500;600",
     '\'"Playfair Display"\', \'Georgia\', \'serif\'',
     '\'Inter\', \'system-ui\', \'sans-serif\'',
     dict(paper="#0f0f0f", panel="#1a1a1a", ink="#f5f5f4", mute="#a3a3a3",
          line="#2a2a2a", accent="#c9a227", accent_soft="#1f1c14")),

    ("bloom", "Bloom", "Soft and feminine. Blush paper, rose accent, Fraunces display.",
     "Fraunces:opsz,wght@9..144,400;9..144,600&family=Nunito+Sans:wght@400;600;700",
     '\'Fraunces\', \'Georgia\', \'serif\'',
     '\'"Nunito Sans"\', \'system-ui\', \'sans-serif\'',
     dict(paper="#fdf6f7", panel="#ffffff", ink="#3d2b2e", mute="#9b8487",
          line="#f0dfe2", accent="#d4869a", accent_soft="#faeef1")),

    ("grove", "Grove", "Earthy and organic. Warm oat tones, sage accent, Spectral serif.",
     "Spectral:wght@400;500;600&family=Karla:wght@400;500;600;700",
     '\'Spectral\', \'Georgia\', \'serif\'',
     '\'Karla\', \'system-ui\', \'sans-serif\'',
     dict(paper="#f6f4ee", panel="#fffdf8", ink="#2b2a24", mute="#7a766a",
          line="#e2ddcf", accent="#5a7d5a", accent_soft="#eaf0e6")),

    ("cobalt", "Cobalt", "Bold and modern. Cool greys, electric blue, Space Grotesk.",
     "Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600",
     '\'"Space Grotesk"\', \'system-ui\', \'sans-serif\'',
     '\'Inter\', \'system-ui\', \'sans-serif\'',
     dict(paper="#f7f8fa", panel="#ffffff", ink="#101828", mute="#667085",
          line="#e4e7ec", accent="#2f5bff", accent_soft="#eaefff")),

    ("sunbaked", "Sunbaked", "Warm terracotta. Marcellus + Work Sans, copper accent.",
     "Marcellus&family=Work+Sans:wght@400;500;600;700",
     '\'Marcellus\', \'Georgia\', \'serif\'',
     '\'"Work Sans"\', \'system-ui\', \'sans-serif\'',
     dict(paper="#fdf6ee", panel="#fffaf2", ink="#3a2417", mute="#8a6f5c",
          line="#ecdcc8", accent="#c1602a", accent_soft="#f7e8db")),

    ("marble", "Marble", "Cool premium neutral. Cormorant + Manrope, stone accent.",
     "Cormorant+Garamond:wght@500;600;700&family=Manrope:wght@400;500;600;700",
     '\'"Cormorant Garamond"\', \'Georgia\', \'serif\'',
     '\'Manrope\', \'system-ui\', \'sans-serif\'',
     dict(paper="#f5f5f4", panel="#ffffff", ink="#292524", mute="#78716c",
          line="#e7e5e4", accent="#57534e", accent_soft="#efedec")),

    ("neon", "Neon", "High-energy streetwear. Near-black ground, lime accent, heavy Archivo.",
     "Archivo:wght@400;600;800;900",
     '\'"Archivo"\', \'"Helvetica Neue"\', \'sans-serif\'',
     '\'"Archivo"\', \'system-ui\', \'sans-serif\'',
     dict(paper="#0d0d0d", panel="#161616", ink="#fafafa", mute="#9a9a9a",
          line="#262626", accent="#c6f24e", accent_soft="#1c2110")),

    ("linen", "Linen", "Minimal Scandinavian. Off-white, taupe accent, EB Garamond.",
     "EB+Garamond:wght@400;500;600&family=Inter:wght@400;500;600",
     '\'"EB Garamond"\', \'Georgia\', \'serif\'',
     '\'Inter\', \'system-ui\', \'sans-serif\'',
     dict(paper="#faf9f6", panel="#ffffff", ink="#1f1f1d", mute="#7c7c76",
          line="#e8e6df", accent="#9c8b73", accent_soft="#f0ede6")),

    ("coral", "Coral", "Vibrant playful DTC. Cream paper, coral accent, all-Poppins.",
     "Poppins:wght@400;500;600;700",
     '\'Poppins\', \'system-ui\', \'sans-serif\'',
     '\'Poppins\', \'system-ui\', \'sans-serif\'',
     dict(paper="#fff8f5", panel="#ffffff", ink="#2d2320", mute="#8f7a72",
          line="#ffe4d8", accent="#ff5a3c", accent_soft="#ffe9e3")),

    # Converted from sd-ecomm-impact (Khushbu Jewellers) — deep maroon + gold,
    # Cormorant Garamond + Lato, small-radius uppercase buttons.
    ("impact", "Impact",
     "Elegant jewellery — deep maroon & gold, Cormorant Garamond over Lato.",
     "Cormorant+Garamond:ital,wght@0,500;0,600;0,700;1,500&family=Lato:wght@400;500;600;700",
     '\'"Cormorant Garamond"\', \'"Playfair Display"\', \'Georgia\', \'serif\'',
     '\'Lato\', \'Montserrat\', \'system-ui\', \'sans-serif\'',
     # ink is a very dark maroon so the hero / footer / dark buttons read maroon
     dict(paper="#faf8f6", panel="#ffffff", ink="#2a0e18", mute="#6b5158",
          line="#e8ded9", accent="#5c1a2e", accent_soft="#f3e9e0"),
     ".font-display{letter-spacing:0;}\n"
     "    a[class*='px-9'],a[class*='px-8'],button{letter-spacing:.06em;}"),

    # Converted from sd-ecomm-impact 2 (Impact Sound) — bold Barlow, near-black
    # on light grey, yellow secondary, pill buttons.
    ("impact2", "Impact Bold",
     "Loud audio/streetwear — heavy Barlow, grey ground, violet pop, pill buttons.",
     "Barlow:wght@400;500;600;700;800",
     '\'Barlow\', \'system-ui\', \'sans-serif\'',
     '\'Barlow\', \'system-ui\', \'sans-serif\'',
     # violet accent for link/price pop (black would collide with body text);
     # yellow stays as accent-soft for bg blocks
     dict(paper="#f0f0f0", panel="#ffffff", ink="#151515", mute="#5a5a5a",
          line="#dcdcdc", accent="#6d28d9", accent_soft="#f0c417"),
     "h1,h2,h3,.font-display{font-weight:800;letter-spacing:-.025em;}\n"
     "    button,a[class*='tracking-luxe'][class*='px-']{border-radius:3.75rem!important;}\n"
     "    .bg-accent-soft{color:#151515;}"),

    # Converted from kapiva-clone — Ayurveda / wellness storefront: warm cream,
    # deep forest green, gold, Fraunces + Outfit, pill buttons, radial glow.
    ("kapiva", "Botanica",
     "Wellness & Ayurveda — cream + forest green + gold, Fraunces over Outfit.",
     "Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Outfit:wght@400;500;600;700",
     '\'Fraunces\', \'Georgia\', \'serif\'',
     '\'Outfit\', \'system-ui\', \'sans-serif\'',
     dict(paper="#f6efe4", panel="#fffdf8", ink="#2c2218", mute="#7a6a52",
          line="#e6dcc6", accent="#163524", accent_soft="#f0e6cf"),
     "body{background:radial-gradient(1100px 480px at 0% -10%,rgba(196,154,74,.10),transparent 50%),#f6efe4;}\n"
     "    h1,h2,.font-display{font-optical-sizing:auto;letter-spacing:0;}\n"
     "    button,a[class*='tracking-luxe'][class*='px-']{border-radius:999px!important;}"),
]


def _render_base(default_base, preset):
    slug, label, desc, fonts_q, disp_stack, sans_stack, c = preset[:7]
    extra_css = preset[7] if len(preset) > 7 else ""
    out = default_base

    out = re.sub(
        r'<link href="https://fonts\.googleapis\.com/css2\?[^"]*" rel="stylesheet">',
        f'<link href="https://fonts.googleapis.com/css2?family={fonts_q}&display=swap" rel="stylesheet">',
        out, count=1,
    )
    out = re.sub(
        r"fontFamily: \{[^}]*\},",
        f"fontFamily: {{ display: [{disp_stack}], sans: [{sans_stack}] }},",
        out, count=1,
    )
    out = re.sub(
        r"colors: \{.*?\},",
        (
            "colors: {\n"
            f"          paper: '{c['paper']}', panel: '{c['panel']}', ink: '{c['ink']}', mute: '{c['mute']}',\n"
            f"          line: '{c['line']}', accent: '{{{{ accent or \"{c['accent']}\" }}}}', 'accent-soft': '{c['accent_soft']}',\n"
            "        },"
        ),
        out, count=1, flags=re.DOTALL,
    )
    out = re.sub(
        r":root \{ --accent: \{\{ accent or '#[0-9a-fA-F]+' \}\}; \}",
        f":root {{ --accent: {{{{ accent or '{c['accent']}' }}}}; }}"
        + (f"\n{extra_css}" if extra_css else ""),
        out, count=1,
    )
    out = re.sub(
        r"body \{ background:#[0-9a-fA-F]+; color:#[0-9a-fA-F]+; font-family:[^;]+;",
        f"body {{ background:{c['paper']}; color:{c['ink']}; "
        f"font-family:{sans_stack.replace(chr(39), '')};",
        out, count=1,
    )
    out = re.sub(
        r'h1,h2,\.font-display \{ font-family:[^;]+; \}',
        f"h1,h2,.font-display {{ font-family:{disp_stack.replace(chr(39), '')}; }}",
        out, count=1,
    )
    return f"{{# {label} skin — {desc} #}}\n" + out


class Command(BaseCommand):
    help = "Generate the built-in MVP storefront skins (palette + font overrides of default)."

    def add_arguments(self, parser):
        parser.add_argument("--activate", metavar="SLUG",
                            help="Point a store's ThemeSettings at this skin.")
        parser.add_argument("--project", default="acme-store")

    def handle(self, *args, **opts):
        default_base_path = SKINS_DIR / "default" / "base.jinja"
        if not default_base_path.exists():
            raise CommandError("templates/shopfront/skins/default/base.jinja is missing.")
        default_base = default_base_path.read_text()

        made = []
        for preset in PRESETS:
            slug, label, desc = preset[0], preset[1], preset[2]
            folder = SKINS_DIR / slug
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "base.jinja").write_text(_render_base(default_base, preset))

            skin, created = Skin.objects.update_or_create(
                slug=slug,
                defaults=dict(
                    label=label, description=desc,
                    source=SkinSource.BUILTIN, is_sandboxed=False,
                    status=SkinStatus.APPROVED, is_active=True,
                    project=None,
                ),
            )
            made.append(slug)
            self.stdout.write(f"  {'+' if created else '~'} {slug:10} {label}")

        self.stdout.write(self.style.SUCCESS(f"{len(made)} built-in skins ready."))

        if opts["activate"]:
            slug = opts["activate"]
            skin = Skin.objects.filter(slug=slug).first()
            if skin is None:
                raise CommandError(f"No skin {slug!r}.")
            try:
                project = Project.objects.get(slug=opts["project"])
            except Project.DoesNotExist:
                raise CommandError(f"No project {opts['project']!r}.")
            from apps.cms.models import ThemeSettings
            ts, _ = ThemeSettings.objects.get_or_create(project=project)
            ts.skin = skin
            ts.save(update_fields=["skin", "updated_at"])
            self.stdout.write(self.style.SUCCESS(
                f"Activated '{slug}' on {project.name}."
            ))
