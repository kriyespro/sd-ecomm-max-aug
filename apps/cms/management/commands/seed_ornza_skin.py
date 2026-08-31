"""Add the built-in "Ornza" skin — a champagne-gold luxe jewellery storefront
derived from the static html-ornza/ theme.

    python manage.py seed_ornza_skin [--activate --project acme-store]

Ships 3 files (base + home + card); every other page template inherits from
``skins/default/``. Idempotent. Does NOT touch other skins or any store's
current choice unless --activate is passed.
"""

import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.cms.models import Skin, SkinSource, SkinStatus, ThemeSettings
from apps.projects.models import Project

SKINS_DIR = Path(settings.BASE_DIR) / "templates" / "shopfront" / "skins"

# ── Ornza palette (from html-ornza/styles.css :root) ──────────────────
C = dict(
    paper="#fffff0", panel="#faf7f0", ink="#3d3420", mute="#7a6b4a",
    line="#e6d9b8", accent="#c9a55a", accent_soft="#f7e7ce",
)

FONTS_Q = "Playfair+Display:ital,wght@0,400;0,500;0,600;0,700;1,400&family=Poppins:wght@200;300;400;500;600;700"
DISPLAY_STACK = "'\"Playfair Display\"', 'Cormorant Garamond', 'serif'"
SANS_STACK = "'Poppins', 'Montserrat', 'system-ui', 'sans-serif'"

MARQUEE = """  {% set _ann = announcement.heading if announcement else '' %}
  {% set _msgs = [_ann] + ['Cash on delivery across India', 'Premium gift packaging on every order', '7-day easy returns', 'Handcrafted with care', '10,000+ happy customers'] if _ann else ['Cash on delivery across India', 'Premium gift packaging on every order', '7-day easy returns', 'Handcrafted with care', '10,000+ happy customers'] %}
  <div class="ornza-ann bg-gradient-to-r from-[#0a0804] via-[#1a1408] to-[#0a0804] px-5 h-[38px] overflow-hidden flex items-center border-b border-accent/25">
    <div class="ornza-ann-track flex items-center gap-16 w-max">
      {% for _ in range(2) %}
        {% for msg in _msgs %}
        <span class="text-[10px] uppercase tracking-[0.16em] text-accent-soft/85 whitespace-nowrap"><span class="text-accent">&#10022;</span> {{ msg }}</span>
        {% endfor %}
      {% endfor %}
    </div>
  </div>"""

EXTRA_CSS = """    @keyframes ornzaAnn { from { transform: translateX(0); } to { transform: translateX(-50%); } }
    .ornza-ann-track { animation: ornzaAnn 34s linear infinite; }
    .ornza-ann:hover .ornza-ann-track { animation-play-state: paused; }
    .ornza-frame { position: relative; }
    .ornza-frame::after { content:''; position:absolute; inset:14px; border:1px solid rgba(201,165,90,.5); pointer-events:none; }
    @keyframes ornzaFade { from { opacity:0; transform:translateY(18px); } to { opacity:1; transform:none; } }
    .ornza-fade { animation: ornzaFade .9s cubic-bezier(.25,1,.5,1) both; }
    .ornza-hero-img::before { content:''; position:absolute; inset:0; z-index:1;
      background:linear-gradient(to right,#080604 0%,rgba(8,6,4,.25) 55%,transparent 100%); }
    .ornza-hero-img::after { content:''; position:absolute; inset:0; z-index:1;
      background:radial-gradient(ellipse at 62% 42%,rgba(201,168,76,.14) 0%,transparent 65%); }
"""


def _base_from_default(default_base):
    out = default_base
    out = re.sub(
        r'<link href="https://fonts\.googleapis\.com/css2\?[^"]*" rel="stylesheet">',
        f'<link href="https://fonts.googleapis.com/css2?family={FONTS_Q}&display=swap" rel="stylesheet">',
        out, count=1,
    )
    out = re.sub(
        r"fontFamily: \{[^}]*\},",
        f"fontFamily: {{ display: [{DISPLAY_STACK}], sans: [{SANS_STACK}] }},",
        out, count=1,
    )
    out = re.sub(
        r"colors: \{.*?\},",
        (
            "colors: {\n"
            f"          paper: '{C['paper']}', panel: '{C['panel']}', ink: '{C['ink']}', mute: '{C['mute']}',\n"
            f"          line: '{C['line']}', accent: '{{{{ accent or \"{C['accent']}\" }}}}', 'accent-soft': '{C['accent_soft']}',\n"
            "        },"
        ),
        out, count=1, flags=re.DOTALL,
    )
    out = re.sub(
        r":root \{ --accent: \{\{ \(accent or '#[0-9a-fA-F]+'\) \| rgb_channels \}\}; \}",
        f":root {{ --accent: {{{{ (accent or '{C['accent']}') | rgb_channels }}}}; }}\n" + EXTRA_CSS,
        out, count=1,
    )
    out = re.sub(
        r"body \{ background:#[0-9a-fA-F]+; color:#[0-9a-fA-F]+; font-family:[^;]+;",
        f"body {{ background:{C['paper']}; color:{C['ink']}; "
        f"font-family:'Poppins',system-ui,sans-serif;",
        out, count=1,
    )
    out = re.sub(
        r'h1,h2,\.font-display \{ font-family:[^;]+; \}',
        'h1,h2,.font-display { font-family:"Playfair Display",Georgia,serif; }',
        out, count=1,
    )
    # scrolling marquee announcement bar
    out = re.sub(
        r"\{% if announcement %\}\s*<div class=\"bg-ink px-4 py-2 text-center.*?</div>\s*\{% endif %\}",
        MARQUEE, out, count=1, flags=re.DOTALL,
    )
    return "{# Ornza skin — champagne-gold luxe jewellery (from html-ornza/) #}\n" + out


HOME = """{% extends "shopfront/base.jinja" %}
{% block title %}{{ store.name }} — Fine jewellery{% endblock %}
{% block content %}

{# ── DARK SPLIT HERO (from html-ornza .hero-v2) ── #}
{% set hero_img = (hero_banner.image.url if (hero_banner and hero_banner.image)
                   else ((featured[0].images.all() | first).image.url if (featured and featured[0].images.all()) else '')) %}
<section class="relative grid min-h-[88vh] overflow-hidden bg-[#080604] text-paper lg:min-h-screen lg:grid-cols-2">
  <div class="relative z-10 flex flex-col justify-center px-6 py-20 sm:px-10 lg:pl-[8%] lg:pr-[6%]">
    <p class="ornza-fade flex items-center gap-3 text-[10px] font-medium uppercase tracking-[0.35em] text-accent" style="animation-delay:.15s">
      <span class="h-px w-8 flex-shrink-0 bg-accent"></span>{{ hero_banner.subheading if hero_banner else 'Fine jewellery, made by hand' }}
    </p>
    <h1 class="ornza-fade mt-6 font-display text-[clamp(2.6rem,6vw,5rem)] font-normal leading-[1.06] tracking-[0.01em] text-[#FFFFF0]" style="animation-delay:.3s">
      {% if hero_banner and hero_banner.heading %}{{ hero_banner.heading }}{% else %}Where craft<br>meets <em class="italic text-[#E8D5A0]">brilliance</em>{% endif %}
    </h1>
    <p class="ornza-fade mt-6 max-w-sm text-sm font-light leading-[1.9] text-white/60" style="animation-delay:.45s">
      Handcrafted pieces that capture the essence of luxury — considered detail and champagne-gold finishes, made for every occasion.
    </p>
    <div class="ornza-fade mt-10 flex flex-wrap gap-3.5" style="animation-delay:.6s">
      <a href="{{ hero_banner.cta_url if (hero_banner and hero_banner.cta_url) else url('shopfront:shop') }}"
         class="inline-flex items-center gap-2.5 rounded-sm bg-accent px-8 py-3.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-[#080604] transition hover:-translate-y-0.5 hover:bg-[#E8D5A0]">
        {{ hero_banner.cta_label if (hero_banner and hero_banner.cta_label) else 'Shop the collection' }}
        <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
      </a>
      <a href="{{ url('shopfront:shop') }}?sort=rating"
         class="inline-flex items-center gap-2.5 rounded-sm border border-white/35 px-8 py-3.5 text-[10px] font-medium uppercase tracking-[0.18em] text-[#FFFFF0] transition hover:border-accent hover:text-accent">
        Best rated
      </a>
    </div>
    <div class="ornza-fade mt-14 flex gap-8 border-t border-white/10 pt-8" style="animation-delay:.75s">
      {% for num, label in [('10K+', 'Happy customers'), (categories | length ~ '+', 'Collections'), ('4.9', 'Average rating')] %}
      <div>
        <p class="font-display text-2xl text-[#FFFFF0]">{{ num }}</p>
        <p class="mt-1 text-[9px] uppercase tracking-[0.15em] text-white/45">{{ label }}</p>
      </div>
      {% endfor %}
    </div>
  </div>
  <div class="ornza-hero-img relative hidden lg:block">
    {% if hero_img %}<img src="{{ hero_img }}" alt="" class="h-full w-full object-cover object-[center_top]">
    {% else %}<div class="h-full w-full bg-gradient-to-br from-[#1a1408] to-[#080604]"></div>{% endif %}
  </div>
</section>

{# ── TRUST STRIP ── #}
<div class="border-y border-line bg-[#fefcf7]">
  <div class="mx-auto grid max-w-7xl grid-cols-2 divide-x divide-line sm:grid-cols-3 lg:grid-cols-5">
    {% for t, s in [('Secure payments', 'UPI, cards & net banking'), ('Easy returns', '7-day hassle-free'), ('Cash on delivery', 'Pay when you receive'), ('Premium packaging', 'Gift-ready every order'), ('10,000+ customers', 'Trusted across India')] %}
    <div class="flex items-center gap-3 px-5 py-5">
      <span class="grid h-9 w-9 flex-shrink-0 place-items-center rounded-full border border-accent/30 bg-accent/10 text-accent">&#10022;</span>
      <div>
        <p class="text-[11px] font-semibold leading-tight text-ink">{{ t }}</p>
        <p class="mt-0.5 text-[9px] uppercase tracking-[0.1em] text-mute">{{ s }}</p>
      </div>
    </div>
    {% endfor %}
  </div>
</div>

{# ── CATEGORIES ── #}
{% if cat_tiles %}
<section class="mx-auto max-w-7xl px-5 py-16 lg:px-8">
  <h2 class="mb-10 text-center font-display text-3xl sm:text-4xl">Shop by category</h2>
  <div class="grid grid-cols-2 gap-4 md:grid-cols-3">
    {% for t in cat_tiles %}
    <a href="{{ url('shopfront:shop') }}?category={{ t.category.slug }}" class="group relative block aspect-[3/2] overflow-hidden bg-line/40">
      {% if t.image %}<img src="{{ t.image }}" alt="{{ t.category.name }}" class="card-img h-full w-full object-cover">{% endif %}
      <div class="absolute inset-0 bg-ink/30 transition-colors group-hover:bg-ink/40"></div>
      <span class="absolute inset-0 flex items-center justify-center font-display text-2xl text-paper">{{ t.category.name }}</span>
    </a>
    {% endfor %}
  </div>
</section>
{% endif %}

{# ── NEW ARRIVALS ── #}
{% if new_arrivals %}
<section class="bg-panel">
  <div class="mx-auto max-w-7xl px-5 py-16 lg:px-8">
    <div class="mb-10 flex items-end justify-between">
      <h2 class="font-display text-3xl sm:text-4xl">New arrivals</h2>
      <a href="{{ url('shopfront:shop') }}?sort=new" class="text-[12px] font-semibold uppercase tracking-luxe text-accent hover:underline">View all</a>
    </div>
    <div class="grid grid-cols-2 gap-x-6 gap-y-10 md:grid-cols-3 lg:grid-cols-4">
      {% for p in new_arrivals %}{% include "shopfront/partials/_card.jinja" %}{% endfor %}
    </div>
  </div>
</section>
{% endif %}

{# ── BEST SELLERS ── #}
<section class="mx-auto max-w-7xl px-5 py-16 lg:px-8">
  <div class="mb-10 flex items-end justify-between">
    <h2 class="font-display text-3xl sm:text-4xl">Best sellers</h2>
    <a href="{{ url('shopfront:shop') }}" class="text-[12px] font-semibold uppercase tracking-luxe text-accent hover:underline">View all</a>
  </div>
  <div class="grid grid-cols-2 gap-x-6 gap-y-10 md:grid-cols-3 lg:grid-cols-4">
    {% for p in featured %}{% include "shopfront/partials/_card.jinja" %}{% endfor %}
  </div>
</section>

{# ── DARK CALLOUT ── #}
<section class="bg-[#1a1408] text-paper">
  <div class="mx-auto max-w-4xl px-5 py-20 text-center lg:px-8">
    <p class="text-[11px] font-semibold uppercase tracking-luxe text-accent">Made in Surat</p>
    <h2 class="mx-auto mt-4 max-w-2xl font-display text-3xl leading-snug sm:text-4xl">Crafted with love, finished by hand, guaranteed for life.</h2>
    <a href="{{ url('shopfront:shop') }}" class="mt-8 inline-block border border-accent px-8 py-3.5 text-[11px] font-semibold uppercase tracking-luxe text-accent transition-colors hover:bg-accent hover:text-ink">Explore the range</a>
  </div>
</section>

{# ── TESTIMONIALS ── #}
{% if testimonials %}
<section class="mx-auto max-w-7xl px-5 py-16 lg:px-8">
  <h2 class="mb-10 text-center font-display text-3xl sm:text-4xl">What our customers say</h2>
  <div class="grid gap-8 md:grid-cols-3">
    {% for r in testimonials %}
    <figure class="ornza-frame border border-line bg-panel p-10 text-center">
      <p class="text-accent">{{ '★' * r.rating }}</p>
      <blockquote class="mt-3 font-display text-lg leading-snug text-ink/85">“{{ r.body | truncate(140) }}”</blockquote>
      <figcaption class="mt-4 text-xs uppercase tracking-luxe text-mute">{{ r.author_name }} · {{ r.product.title }}</figcaption>
    </figure>
    {% endfor %}
  </div>
</section>
{% endif %}

{# ── VALUE PROPS ── #}
<section class="border-y border-line bg-panel">
  <div class="mx-auto grid max-w-7xl gap-8 px-5 py-14 text-center sm:grid-cols-3 lg:px-8">
    {% for t, d in [('Free shipping', 'On all orders over ₹999'), ('Cash on delivery', 'Available across India'), ('7-day returns', 'Easy, no-questions returns')] %}
    <div>
      <p class="font-display text-xl">{{ t }}</p>
      <p class="mt-1 text-sm text-mute">{{ d }}</p>
    </div>
    {% endfor %}
  </div>
</section>
{% endblock %}
"""

CARD = """{# Ornza product card — expects `p` #}
{% set img = (p.images.all() | first) %}
<article class="group relative">
  <a href="{{ url('shopfront:product', kwargs={'slug': p.slug}) }}" class="block overflow-hidden bg-panel">
    <div class="ornza-frame aspect-[4/5] overflow-hidden bg-line/40">
      {% if img %}
      <img src="{{ img.image.url }}" alt="{{ img.alt or p.title }}" class="card-img h-full w-full object-cover">
      {% else %}
      <div class="flex h-full items-center justify-center text-4xl text-accent/50">&#9670;</div>
      {% endif %}
    </div>
  </a>

  {% if p.on_sale %}<span class="absolute left-3 top-3 bg-accent px-2 py-1 text-[10px] font-semibold uppercase tracking-luxe text-ink">Sale</span>
  {% elif p.is_new_arrival %}<span class="absolute left-3 top-3 bg-ink px-2 py-1 text-[10px] font-semibold uppercase tracking-luxe text-paper">New</span>{% endif %}

  <button hx-get="{{ url('shopfront:quickview', kwargs={'slug': p.slug}) }}" hx-target="#quickview-body" hx-swap="innerHTML"
          @click="$dispatch('open-quick')" title="Quick view"
          class="absolute right-3 top-3 grid h-9 w-9 place-items-center bg-paper/90 text-ink opacity-0 backdrop-blur transition-opacity group-hover:opacity-100 hover:bg-accent">
    <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
  </button>

  <form hx-post="{{ url('shopfront:cart_add') }}" hx-swap="none"
        class="absolute inset-x-3 bottom-[4.5rem] translate-y-2 opacity-0 transition-all duration-300 group-hover:translate-y-0 group-hover:opacity-100">
    <input type="hidden" name="product" value="{{ p.slug }}">
    <button class="w-full bg-ink/92 py-2.5 text-[11px] font-semibold uppercase tracking-luxe text-paper backdrop-blur hover:bg-accent hover:text-ink">
      Add to bag
    </button>
  </form>

  <div class="mt-4 text-center">
    <a href="{{ url('shopfront:product', kwargs={'slug': p.slug}) }}" class="block font-display text-lg leading-tight hover:text-accent">{{ p.title }}</a>
    {% if p.rating_count %}
    <p class="mt-1 text-xs text-accent">{{ '★' * (p.rating_avg | round | int) }}<span class="text-mute"> ({{ p.rating_count }})</span></p>
    {% endif %}
    <p class="mt-1.5 text-sm">
      {% if p.on_sale %}
      <span class="text-mute line-through">{{ p.price | money }}</span>
      <span class="ml-1 font-medium text-accent">{{ p.current_price | money }}</span>
      {% else %}
      <span class="font-medium text-ink">{{ p.price | money }}</span>
      {% endif %}
    </p>
  </div>
</article>
"""


class Command(BaseCommand):
    help = "Add the built-in 'Ornza' champagne-gold jewellery skin."

    def add_arguments(self, parser):
        parser.add_argument("--activate", action="store_true")
        parser.add_argument("--project", default="acme-store")
        parser.add_argument(
            "--force", action="store_true",
            help="Also overwrite home.jinja / _card.jinja (they carry hand-tuned "
                 "perf attributes; by default a re-run only refreshes base.jinja).",
        )

    def handle(self, *args, **opts):
        default_base = (SKINS_DIR / "default" / "base.jinja")
        if not default_base.exists():
            raise CommandError("skins/default/base.jinja missing — run makemigrations/setup first.")

        folder = SKINS_DIR / "ornza"
        (folder / "partials").mkdir(parents=True, exist_ok=True)
        (folder / "base.jinja").write_text(_base_from_default(default_base.read_text()))
        for rel, body in (("home.jinja", HOME), ("partials/_card.jinja", CARD)):
            dest = folder / rel
            if opts["force"] or not dest.exists():
                dest.write_text(body)

        skin, created = Skin.objects.update_or_create(
            slug="ornza",
            defaults=dict(
                label="Ornza",
                description="Champagne-gold luxe jewellery — Playfair Display + Poppins, ivory & espresso.",
                source=SkinSource.BUILTIN, is_sandboxed=False,
                status=SkinStatus.APPROVED, is_active=True, project=None,
            ),
        )
        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} skin 'ornza' (base + home + card)."
        ))

        if opts["activate"]:
            try:
                project = Project.objects.get(slug=opts["project"])
            except Project.DoesNotExist:
                raise CommandError(f"No project {opts['project']!r}.")
            ts, _ = ThemeSettings.objects.get_or_create(project=project)
            ts.skin = skin
            ts.save(update_fields=["skin", "updated_at"])
            self.stdout.write(self.style.SUCCESS(f"Activated 'ornza' on {project.name}."))
