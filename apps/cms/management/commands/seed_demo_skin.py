"""Seed the demo uploaded skin "jwdemo" — a faithful clone of the sd-jw-demo1
("Diamo") jewellery storefront, adapted to the sandboxed skin data contract.

    python manage.py seed_demo_skin --project acme-store --activate

Runs the real upload pipeline (zip -> validate -> SkinFile rows), then approves
it and (with --activate) points the store's ThemeSettings at it.
"""

import io
import json
import zipfile

from django.core.management.base import BaseCommand, CommandError

from apps.cms.models import Skin, SkinStatus, ThemeSettings
from apps.cms.skin_upload import create_skin_from_upload
from apps.projects.models import Project

STAR = ('<svg class="w-3.5 h-3.5 text-amber-400" fill="currentColor" viewBox="0 0 20 20">'
        '<path d="M9.05 2.93c.3-.92 1.6-.92 1.9 0l1.07 3.29a1 1 0 00.95.69h3.46c.97 0 1.37 1.24.59 1.81l-2.8 2.03a1 1 0 00-.37 1.12l1.07 3.29c.3.92-.75 1.69-1.54 1.12l-2.8-2.03a1 1 0 00-1.17 0l-2.8 2.03c-.78.57-1.84-.2-1.54-1.12l1.07-3.29a1 1 0 00-.37-1.12l-2.8-2.03c-.78-.57-.38-1.81.59-1.81h3.46a1 1 0 00.95-.69l1.07-3.29z"/></svg>')

BASE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}{{ store.name }}{% endblock %}</title>
<script src="https://cdn.tailwindcss.com"></script>
<script>
tailwind.config = { theme: { extend: {
  colors: {
    ink: { DEFAULT: '#1c1917', soft: '#44403c', mute: '#78716c' },
    sand: '#f7f5f2',
    accent: '{{ store.accent or "#1c1917" }}',
  },
  fontFamily: { serif: ['"Cormorant Garamond"','Georgia','serif'], sans: ['"Manrope"','system-ui','sans-serif'] },
}}};
</script>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=Manrope:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://unpkg.com/htmx.org@1.9.12"></script>
<script defer src="https://unpkg.com/alpinejs@3.14.1/dist/cdn.min.js"></script>
<style>
  [x-cloak]{display:none!important}
  body{font-family:'Manrope',system-ui,sans-serif;background:#fff;color:#1c1917;-webkit-font-smoothing:antialiased}
  .font-serif{font-family:'Cormorant Garamond',Georgia,serif;font-weight:600;letter-spacing:.01em}
  .tracked{letter-spacing:.14em}
  .scrollbar-hide::-webkit-scrollbar{display:none}
  .scrollbar-hide{-ms-overflow-style:none;scrollbar-width:none}
  @keyframes heroZoom{from{transform:scale(1)}to{transform:scale(1.08)}}
  .hero-zoom{animation:heroZoom 18s ease-out forwards}
  .line-clamp-2{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
</style>
{% block head %}{% endblock %}
</head>
<body class="bg-white text-ink antialiased" hx-headers='{"X-CSRFToken":"{{ csrf_token }}"}'>
<div class="hidden">{{ csrf_input }}</div>

<div class="bg-ink text-white text-[11px] py-2 px-4 text-center tracked uppercase">
  {{ store.announcement.text if store.announcement else 'Certified quality · Free shipping · 7-day easy returns' }}
</div>

<header class="sticky top-0 z-50 bg-white border-b border-stone-100" x-data="{ mobileOpen:false, cartOpen:false }" @cart-open.window="cartOpen=true">
  <div class="max-w-7xl mx-auto px-4 sm:px-6">
    <div class="flex items-center gap-4 h-14">
      <button @click="mobileOpen=!mobileOpen" class="lg:hidden p-1.5 text-ink-soft">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/></svg>
      </button>
      <a href="{{ url('shopfront:home') }}" class="font-serif text-xl text-ink flex-shrink-0">{{ store.name }}</a>
      <nav class="hidden lg:flex items-center gap-1 flex-1">
        <a href="{{ url('shopfront:shop') }}" class="px-3 py-1.5 text-[13px] font-medium text-ink-soft hover:text-ink">Shop All</a>
        {% for m in store.menu[:6] %}<a href="{{ m.url }}" class="px-3 py-1.5 text-[13px] font-medium text-ink-soft hover:text-ink whitespace-nowrap">{{ m.label }}</a>{% endfor %}
      </nav>
      <div class="relative hidden sm:block w-52 ml-auto" @click.outside="$el.querySelector('#hdr-suggest').innerHTML=''">
        <form method="get" action="{{ url('shopfront:shop') }}">
          <input name="q" placeholder="Search" autocomplete="off"
                 hx-get="{{ url('shopfront:search_suggest') }}" hx-trigger="input changed delay:250ms" hx-target="#hdr-suggest"
                 class="w-full h-8 px-3 border border-stone-200 rounded text-xs bg-stone-50 focus:outline-none focus:border-stone-400">
        </form>
        <div id="hdr-suggest" class="absolute right-0 top-full mt-1 w-72 z-50"></div>
      </div>
      <a href="{{ url('shopfront:account') }}" class="p-1.5 text-ink-soft hover:text-ink hidden sm:block">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>
      </a>
      <button @click="cartOpen=!cartOpen" class="relative p-1.5 text-ink-soft hover:text-ink flex-shrink-0">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-width="2" d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z"/></svg>
        <span class="absolute -top-0.5 -right-0.5 bg-ink text-white text-[9px] rounded-full min-w-[15px] h-4 px-0.5 flex items-center justify-center font-semibold" id="cart-count">{{ cart.item_count if cart else 0 }}</span>
      </button>
    </div>
  </div>
  <div x-show="mobileOpen" x-cloak class="lg:hidden border-t border-stone-100 px-2 py-2">
    <a href="{{ url('shopfront:shop') }}" class="block px-3 py-2 text-sm text-ink-soft hover:bg-sand">Shop All</a>
    {% for m in store.menu %}<a href="{{ m.url }}" class="block px-3 py-2 text-sm text-ink-soft hover:bg-sand">{{ m.label }}</a>{% endfor %}
  </div>

  <div x-show="cartOpen" x-cloak class="fixed inset-0 z-50" style="display:none">
    <div class="absolute inset-0 bg-black/50" @click="cartOpen=false"></div>
    <div class="absolute right-0 top-0 h-full w-full max-w-sm bg-white shadow-xl flex flex-col"
         hx-get="{{ url('shopfront:cart_drawer') }}" hx-trigger="load, cart-open from:body" hx-target="#cart-drawer-body" hx-swap="outerHTML">
      <div class="flex items-center justify-between p-4 border-b border-stone-100">
        <h2 class="font-serif text-lg">Your Cart</h2>
        <button @click="cartOpen=false" class="p-1 text-stone-400 hover:text-ink">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
        </button>
      </div>
      {% include "partials/_cart_drawer.jinja" %}
    </div>
  </div>
</header>

{% for m in messages %}<div class="bg-sand text-ink-soft text-sm py-2.5 px-4 text-center">{{ m.text }}</div>{% endfor %}

<main>{% block content %}{% endblock %}</main>

<footer class="bg-ink text-white mt-16">
  <div class="max-w-7xl mx-auto px-4 sm:px-6 py-12 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-8">
    <div>
      <span class="font-serif text-2xl">{{ store.name }}</span>
      <p class="text-white/50 text-sm leading-relaxed mt-4">Fine craftsmanship, considered detail. Trusted quality in every piece.</p>
    </div>
    <div>
      <h4 class="font-serif text-sm mb-4 tracked uppercase text-amber-400">Shop</h4>
      <ul class="space-y-2">
        <li><a href="{{ url('shopfront:shop') }}" class="text-white/50 hover:text-white text-sm">All products</a></li>
        {% for m in store.menu[:6] %}<li><a href="{{ m.url }}" class="text-white/50 hover:text-white text-sm">{{ m.label }}</a></li>{% endfor %}
      </ul>
    </div>
    <div>
      <h4 class="font-serif text-sm mb-4 tracked uppercase text-amber-400">Help</h4>
      <ul class="space-y-2">
        {% for l in store.footer_links %}<li><a href="{{ l.url }}" class="text-white/50 hover:text-white text-sm">{{ l.title }}</a></li>{% endfor %}
        <li><a href="{{ url('shopfront:track') }}" class="text-white/50 hover:text-white text-sm">Track order</a></li>
        <li><a href="{{ url('shopfront:account') }}" class="text-white/50 hover:text-white text-sm">Account</a></li>
      </ul>
    </div>
    <div>
      <h4 class="font-serif text-sm mb-4 tracked uppercase text-amber-400">Newsletter</h4>
      <p class="text-white/50 text-sm mb-3">Early access to new pieces and offers.</p>
      <form method="get" action="{{ url('shopfront:account') }}" class="flex gap-2">
        <input type="email" placeholder="Email" class="flex-1 px-3 py-2 text-sm text-ink bg-white rounded">
        <button class="bg-amber-400 text-ink text-xs font-bold px-3 rounded uppercase tracked">Join</button>
      </form>
    </div>
  </div>
  <div class="border-t border-white/10">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 py-4 text-center">
      <p class="text-white/40 text-xs">&copy; {{ store.name }} &mdash; jwdemo skin</p>
    </div>
  </div>
</footer>

{% block scripts %}{% endblock %}
</body></html>
"""

CARD = """<div class="group">
  <a href="{{ p.url }}" class="block relative">
    <div class="aspect-square bg-sand overflow-hidden">
      {% if p.images %}<img src="{{ p.images[0].url }}" alt="{{ p.images[0].alt or p.title }}" class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500" loading="lazy">
      {% else %}<div class="w-full h-full flex items-center justify-center text-stone-300">
        <svg class="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-width="1" d="M4 16l4.6-4.6a2 2 0 012.8 0L16 16m-2-2l1.6-1.6a2 2 0 012.8 0L20 14M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
      </div>{% endif %}
    </div>
    {% if p.on_sale %}<span class="absolute top-2 left-2 bg-ink text-white text-[10px] font-semibold px-2 py-0.5 rounded-sm">Sale &minus;{{ p.discount_pct }}%</span>
    {% elif p.is_new_arrival %}<span class="absolute top-2 left-2 bg-white/95 text-ink-soft text-[10px] font-semibold px-2 py-0.5 rounded-sm border border-stone-200">New</span>{% endif %}
  </a>
  <div class="pt-3">
    <a href="{{ p.url }}"><h3 class="text-sm font-medium text-ink leading-snug line-clamp-2 hover:text-ink-mute">{{ p.title }}</h3></a>
    <div class="flex items-center gap-1.5 mt-1.5">
      <div class="flex">""" + STAR * 5 + """</div>
      <span class="text-xs text-ink-mute">{{ p.rating_avg | round(1) if p.rating_avg else '5.0' }}{% if p.rating_count %} / {{ p.rating_count }} reviews{% endif %}</span>
    </div>
    <div class="flex items-baseline gap-2 mt-1.5">
      <span class="text-base font-semibold text-ink">{{ p.current_price | money }}</span>
      {% if p.on_sale %}<span class="text-sm text-stone-400 line-through">{{ p.compare_at_price | money }}</span>{% endif %}
    </div>
    <form hx-post="{{ url('shopfront:cart_add') }}" hx-target="#cart-count" hx-swap="outerHTML" class="mt-2">
      {{ csrf_input }}<input type="hidden" name="product" value="{{ p.slug }}">
      <button class="w-full border border-ink text-ink text-[11px] font-semibold uppercase tracked py-2 hover:bg-ink hover:text-white transition-colors">Add to cart</button>
    </form>
  </div>
</div>
"""

GRID = '<div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-x-4 gap-y-8">{% for p in products %}{% include "partials/_card.jinja" %}{% endfor %}</div>\n'

HOME = """{% extends "base.jinja" %}
{% block content %}
<section class="relative overflow-hidden bg-stone-900" style="min-height:min(72vh,620px)">
  <div class="absolute inset-0">
    {% if store.hero and store.hero.image_url %}<img src="{{ store.hero.image_url }}" alt="" class="w-full h-full object-cover hero-zoom opacity-70">
    {% elif featured and featured[0].images %}<img src="{{ featured[0].images[0].url }}" alt="" class="w-full h-full object-cover hero-zoom opacity-60">{% endif %}
    <div class="absolute inset-0 bg-gradient-to-r from-black/65 via-black/35 to-transparent"></div>
  </div>
  <div class="relative z-10 max-w-7xl mx-auto px-5 sm:px-8 flex items-center" style="min-height:min(72vh,620px)">
    <div class="max-w-lg py-16">
      <p class="text-white/80 text-[11px] font-medium tracked uppercase mb-4">{{ store.hero.subheading if store.hero else "Season's New Favourites" }}</p>
      <h1 class="font-serif text-4xl sm:text-5xl lg:text-[3.4rem] text-white leading-[1.15] mb-5">{{ store.hero.heading if store.hero else store.name }}</h1>
      <a href="{{ store.hero.cta_url if store.hero and store.hero.cta_url else url('shopfront:shop') }}"
         class="inline-flex items-center border border-white text-white text-xs font-semibold tracked uppercase px-7 py-3 hover:bg-white hover:text-ink transition-all">
        {{ store.hero.cta_label if store.hero and store.hero.cta_label else 'Shop Collection' }}
      </a>
    </div>
  </div>
</section>

<section class="bg-ink text-white">
  <div class="max-w-7xl mx-auto grid grid-cols-2 md:grid-cols-4 divide-x divide-white/10">
    {% for t in ['Free Shipping','Secure Checkout','7-Day Easy Returns','Quality Guaranteed'] %}
    <div class="px-4 py-3.5 text-center text-[11px] sm:text-xs font-medium tracked text-white/90">{{ t }}</div>
    {% endfor %}
  </div>
</section>

{% if category_tiles %}
<section class="py-10 sm:py-12 bg-white">
  <div class="max-w-7xl mx-auto px-4">
    <div class="flex items-start justify-center gap-5 sm:gap-8 overflow-x-auto scrollbar-hide">
      {% for t in category_tiles %}
      <a href="{{ t.url }}" class="flex-shrink-0 flex flex-col items-center gap-3 group w-[150px] sm:w-[180px]">
        <div class="w-[150px] h-[150px] sm:w-[180px] sm:h-[180px] rounded-2xl overflow-hidden bg-sand ring-1 ring-stone-200 group-hover:ring-stone-400 group-hover:shadow-lg transition-all">
          {% if t.image_url %}<img src="{{ t.image_url }}" alt="{{ t.name }}" class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500" loading="lazy">{% endif %}
        </div>
        <span class="text-sm font-medium text-ink-soft text-center leading-tight">{{ t.name }}</span>
      </a>
      {% endfor %}
    </div>
  </div>
</section>
{% endif %}

<section class="pb-14 pt-2">
  <div class="max-w-7xl mx-auto px-4 sm:px-6">
    <div class="flex items-end justify-between mb-7">
      <h2 class="font-serif text-2xl sm:text-3xl text-ink">Best Sellers</h2>
      <a href="{{ url('shopfront:shop') }}" class="text-xs tracked uppercase text-ink-mute hover:text-ink border-b border-stone-300 hover:border-ink pb-0.5">View All</a>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-8">
      {% for p in featured[:8] %}{% include "partials/_card.jinja" %}{% endfor %}
    </div>
  </div>
</section>

<section class="py-10 border-y border-stone-100 bg-white">
  <div class="max-w-5xl mx-auto px-4">
    <div class="grid grid-cols-2 md:grid-cols-4 gap-6">
      {% for label, d in [
        ('FREE SHIPPING','M5 8h10v8H5V8zm10 2h3l2 3v3h-5v-6zM8 19a1 1 0 100-2 1 1 0 000 2zm9 0a1 1 0 100-2 1 1 0 000 2z'),
        ('SECURE PAYMENT','M9 12l2 2 4-4M7.8 4.7a3.4 3.4 0 002-.8 3.4 3.4 0 014.4 0 3.4 3.4 0 002 .8 3.4 3.4 0 013.1 3.1 3.4 3.4 0 00.8 2 3.4 3.4 0 010 4.4 3.4 3.4 0 00-.8 2 3.4 3.4 0 01-3.1 3.1 3.4 3.4 0 00-2 .8 3.4 3.4 0 01-4.4 0 3.4 3.4 0 00-2-.8 3.4 3.4 0 01-3.1-3.1 3.4 3.4 0 00-.8-2 3.4 3.4 0 010-4.4 3.4 3.4 0 00.8-2 3.4 3.4 0 013.1-3.1z'),
        ('EASY RETURNS','M4 4v5h.6m15.3 2A8 8 0 004.6 9m0 0H9m11 11v-5h-.6m0 0a8 8 0 01-15.3-2m15.3 2H15'),
        ('QUALITY GUARANTEED','M9 12l2 2 4-4m5.6-4A11.9 11.9 0 0112 2.9a11.9 11.9 0 01-8.6 3A12 12 0 003 9c0 5.6 3.8 10.3 9 11.6 5.2-1.3 9-6 9-11.6 0-1-.1-2-.4-3z'),
      ] %}
      <div class="flex flex-col items-center text-center gap-2.5">
        <div class="w-11 h-11 rounded-full border border-stone-200 flex items-center justify-center text-ink-soft">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-width="1.5" d="{{ d }}"/></svg>
        </div>
        <p class="text-[11px] font-medium tracked text-ink-soft uppercase">{{ label }}</p>
      </div>
      {% endfor %}
    </div>
  </div>
</section>

{% if new_arrivals %}
<section class="py-14">
  <div class="max-w-7xl mx-auto px-4 sm:px-6">
    <div class="flex items-end justify-between mb-7">
      <h2 class="font-serif text-2xl sm:text-3xl text-ink">New In</h2>
      <a href="{{ url('shopfront:shop') }}?sort=new" class="text-xs tracked uppercase text-ink-mute hover:text-ink border-b border-stone-300 hover:border-ink pb-0.5">View All</a>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-8">
      {% for p in new_arrivals[:4] %}{% include "partials/_card.jinja" %}{% endfor %}
    </div>
  </div>
</section>
{% endif %}

{% if testimonials %}
<section class="py-14 bg-sand">
  <div class="max-w-7xl mx-auto px-4 sm:px-6">
    <div class="text-center mb-8">
      <h2 class="font-serif text-2xl sm:text-3xl text-ink mb-2">Loved By Our Customers</h2>
      <div class="flex items-center justify-center gap-2">
        <div class="flex">""" + STAR * 5 + """</div>
        <span class="text-sm text-ink-soft font-medium">4.9 / 5</span>
      </div>
    </div>
    <div class="flex gap-4 overflow-x-auto scrollbar-hide pb-2">
      {% for t in testimonials %}
      <div class="flex-shrink-0 w-[280px] bg-white border border-stone-100 p-5">
        <div class="flex gap-0.5 mb-3">{% for i in range(t.rating or 5) %}""" + STAR + """{% endfor %}</div>
        <p class="text-sm text-ink-soft leading-relaxed mb-4">&ldquo;{{ t.body }}&rdquo;</p>
        <div class="flex items-center gap-2 pt-3 border-t border-stone-50">
          <div class="w-7 h-7 rounded-full bg-sand flex items-center justify-center text-xs font-semibold text-ink-soft">{{ t.author[0] if t.author else 'A' }}</div>
          <p class="text-xs font-medium text-ink-soft">{{ t.author }}</p>
        </div>
      </div>
      {% endfor %}
    </div>
  </div>
</section>
{% endif %}

<section class="py-14 bg-sand">
  <div class="max-w-7xl mx-auto px-4 sm:px-6 grid md:grid-cols-2 gap-10 lg:gap-16 items-center">
    <div>
      <p class="text-[11px] font-medium tracked uppercase text-ink-mute mb-3">Our Story</p>
      <h2 class="font-serif text-2xl sm:text-3xl text-ink mb-4">The {{ store.name }} Studio</h2>
      <p class="text-sm text-ink-soft leading-relaxed mb-3">We make pieces designed to last — considered materials, careful finishing, honest pricing.</p>
      <p class="text-sm text-ink-soft leading-relaxed mb-6">From the moment you put on a piece, you feel the difference: the weight, the finish, the detail.</p>
      <a href="{{ url('shopfront:shop') }}" class="inline-flex items-center text-xs font-semibold tracked uppercase text-ink border-b border-ink pb-0.5 hover:opacity-60">Know More</a>
    </div>
    {% if featured and featured[0].images %}
    <div class="aspect-[4/3] overflow-hidden bg-stone-200">
      <img src="{{ featured[0].images[0].url }}" alt="" class="w-full h-full object-cover">
    </div>
    {% endif %}
  </div>
</section>

<section class="py-14 border-t border-stone-100" x-data="{ open: 0 }">
  <div class="max-w-3xl mx-auto px-4 sm:px-6">
    <h2 class="font-serif text-2xl sm:text-3xl text-ink text-center mb-8">FAQs</h2>
    {% set faqs = [
      ('What is included with each order?','Every order includes the piece in premium packaging, a care card, and an invoice. Free gift wrapping on request.'),
      ('How long does delivery take?','Standard delivery is 3–5 business days with free shipping. Orders before 2 PM on weekdays dispatch same day.'),
      ('Can I return or exchange?','Yes — 7-day easy returns. Contact us within 7 days of delivery for a free pickup and full refund.'),
      ('How do I care for my piece?','Wipe with a soft cloth, keep away from water and perfume, and store each piece in its own pouch.'),
    ] %}
    {% for q, a in faqs %}
    <div class="border-b border-stone-100">
      <button @click="open === {{ loop.index0 }} ? open = null : open = {{ loop.index0 }}" class="w-full flex items-center justify-between py-4 text-left gap-4">
        <span class="text-sm font-medium text-ink-soft">{{ q }}</span>
        <svg :class="open === {{ loop.index0 }} ? 'rotate-180' : ''" class="w-4 h-4 text-stone-400 flex-shrink-0 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
      </button>
      <div x-show="open === {{ loop.index0 }}" x-cloak class="pb-4"><p class="text-sm text-ink-mute leading-relaxed pr-8">{{ a }}</p></div>
    </div>
    {% endfor %}
  </div>
</section>
{% endblock %}
"""

SHOP = """{% extends "base.jinja" %}
{% block content %}
<section class="relative overflow-hidden bg-stone-900" style="min-height:200px">
  {% if products and products[0].images %}<img src="{{ products[0].images[0].url }}" alt="" class="absolute inset-0 w-full h-full object-cover opacity-40">{% endif %}
  <div class="absolute inset-0 bg-gradient-to-r from-black/70 to-transparent"></div>
  <div class="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 py-12">
    <nav class="flex items-center gap-2 text-xs text-white/70 mb-3">
      <a href="{{ url('shopfront:home') }}" class="hover:text-white">Home</a><span>/</span><span class="text-white">Shop</span>
    </nav>
    <h1 class="font-serif text-3xl sm:text-4xl text-white">{{ filters.active_category or 'All Jewellery' }}</h1>
  </div>
</section>

<div class="max-w-7xl mx-auto px-4 sm:px-6 pt-8 pb-16">
  <form method="get" class="flex items-center justify-between mb-7 gap-3 flex-wrap border-b border-stone-100 pb-4">
    <p class="text-sm text-ink-soft"><span class="font-semibold text-ink">{{ pagination.count if pagination else products|length }}</span> pieces</p>
    <div class="flex gap-2 ml-auto">
      <input name="q" value="{{ filters.query }}" placeholder="Search" class="border border-stone-200 rounded-lg px-3 py-2 text-sm w-40">
      <select name="category" class="border border-stone-200 rounded-lg px-3 py-2 text-sm">
        <option value="">All collections</option>
        {% for c in filters.categories %}<option value="{{ c.slug }}" {% if c.slug == filters.active_category %}selected{% endif %}>{{ c.name }}</option>{% endfor %}
      </select>
      <select name="sort" class="border border-stone-200 rounded-lg px-3 py-2 text-sm">
        {% for s in filters.sorts %}<option value="{{ s.key }}" {% if s.selected %}selected{% endif %}>{{ s.label }}</option>{% endfor %}
      </select>
      <button class="bg-ink text-white text-sm px-4 rounded-lg">Apply</button>
    </div>
  </form>

  {% if products %}
  {% include "partials/_grid.jinja" %}
  {% if pagination %}
  <div class="flex items-center justify-center gap-1 mt-12">
    {% if pagination.prev_url %}<a href="{{ pagination.prev_url }}" class="px-3 py-2 text-sm text-ink-soft border border-stone-200 rounded-lg hover:border-stone-400">&larr;</a>{% endif %}
    <span class="px-3 py-2 text-sm font-semibold bg-ink text-white rounded-lg">{{ pagination.page }}</span>
    <span class="text-sm text-ink-mute px-1">of {{ pagination.pages }}</span>
    {% if pagination.next_url %}<a href="{{ pagination.next_url }}" class="px-3 py-2 text-sm text-ink-soft border border-stone-200 rounded-lg hover:border-stone-400">&rarr;</a>{% endif %}
  </div>
  {% endif %}
  {% else %}
  <div class="text-center py-24"><p class="text-ink-mute text-sm mb-4">No products in this collection yet.</p>
    <a href="{{ url('shopfront:shop') }}" class="text-sm font-semibold text-ink underline">Browse all</a></div>
  {% endif %}
</div>
{% endblock %}
"""

PRODUCT = """{% extends "base.jinja" %}
{% block title %}{{ product.title }} &middot; {{ store.name }}{% endblock %}
{% block content %}
<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-5" x-data="{ open: 'love' }">
  <nav class="flex items-center gap-1.5 text-xs text-stone-400 mb-4 flex-wrap">
    <a href="{{ url('shopfront:home') }}" class="hover:text-ink-soft">Home</a><span>/</span>
    {% if product.category %}<a href="{{ product.category.url }}" class="hover:text-ink-soft">{{ product.category.name }}</a><span>/</span>{% endif %}
    <span class="text-ink-soft">{{ product.title }}</span>
  </nav>

  <div class="grid lg:grid-cols-2 gap-6 xl:gap-10">
    {% if product.images | length > 1 %}
    <div class="grid grid-cols-2 gap-2">
      {% for im in product.images[:4] %}
      <div class="relative bg-white border border-stone-100 rounded overflow-hidden group" style="aspect-ratio:4/5">
        <img src="{{ im.url }}" alt="{{ im.alt or product.title }}" class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500">
      </div>
      {% endfor %}
    </div>
    {% elif product.images %}
    <div class="bg-white border border-stone-100 rounded overflow-hidden" style="aspect-ratio:4/5">
      <img src="{{ product.images[0].url }}" alt="{{ product.images[0].alt or product.title }}" class="w-full h-full object-cover">
    </div>
    {% else %}
    <div class="aspect-square bg-sand flex items-center justify-center text-stone-300 rounded">
      <svg class="w-16 h-16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-width="1" d="M4 16l4.6-4.6a2 2 0 012.8 0L16 16M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
    </div>
    {% endif %}

    <div class="flex flex-col">
      <div class="flex items-baseline gap-2.5 mb-1.5">
        <span class="text-2xl font-bold text-ink">{{ product.current_price | money }}</span>
        {% if product.on_sale %}<span class="text-base text-stone-400 line-through">{{ product.compare_at_price | money }}</span>
          <span class="text-xs font-bold text-white bg-ink px-2 py-0.5 rounded">-{{ product.discount_pct }}%</span>{% endif %}
      </div>
      <h1 class="text-xl sm:text-2xl font-semibold text-ink leading-snug mb-2">{{ product.title }}</h1>
      {% if product.brand %}<p class="text-[13px] text-stone-400 mb-2">{{ product.brand.name }}</p>{% endif %}
      <div class="flex items-center gap-2 mb-4">
        <div class="flex gap-0.5">""" + STAR * 5 + """</div>
        <span class="text-xs text-stone-400">{{ product.rating_avg | round(1) if product.rating_avg else '5.0' }} &middot; <a href="#reviews" class="underline">{{ product.rating_count or 0 }} reviews</a></span>
      </div>
      <p class="text-sm text-ink-soft mb-4">{{ product.short_description }}</p>

      <div class="border-t border-stone-100 pt-4 space-y-4">
        {% if product.variants %}
        <div>
          <p class="text-[13px] font-medium text-ink-soft mb-2">Options</p>
          <form hx-post="{{ url('shopfront:cart_add') }}" hx-target="#cart-count" hx-swap="outerHTML" class="space-y-3">
            {{ csrf_input }}<input type="hidden" name="product" value="{{ product.slug }}">
            <select name="variant" class="border border-stone-200 rounded px-3 py-2 text-sm w-full">
              {% for v in product.variants %}<option value="{{ v.id }}">{{ v.label }} &mdash; {{ v.current_price | money }}</option>{% endfor %}
            </select>
            <button class="w-full bg-ink text-white text-sm font-semibold py-3 rounded hover:bg-ink-soft transition-colors">Add to cart</button>
          </form>
        </div>
        {% else %}
        <form hx-post="{{ url('shopfront:cart_add') }}" hx-target="#cart-count" hx-swap="outerHTML">
          {{ csrf_input }}<input type="hidden" name="product" value="{{ product.slug }}">
          <button class="w-full bg-ink text-white text-sm font-semibold py-3 rounded hover:bg-ink-soft transition-colors">
            {{ 'Add to cart' if product.in_stock else 'Sold out' }}
          </button>
        </form>
        {% endif %}

        <div class="grid grid-cols-4 divide-x divide-stone-100 border border-stone-100 rounded overflow-hidden">
          {% for label in ['Secure Payment','7-Day Returns','Free Shipping','Support'] %}
          <div class="flex flex-col items-center justify-center gap-1.5 py-3 px-1 text-center">
            <svg class="w-5 h-5 text-stone-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-width="1.5" d="M9 12l2 2 4-4"/></svg>
            <p class="text-[9px] text-stone-500 leading-tight">{{ label }}</p>
          </div>
          {% endfor %}
        </div>

        <div class="divide-y divide-stone-100 border-t border-stone-100">
          <div>
            <button @click="open = open === 'love' ? null : 'love'" class="w-full flex items-center justify-between py-3.5 text-left">
              <span class="text-[13px] font-medium text-ink-soft">Description</span>
              <svg :class="open === 'love' ? '' : 'rotate-180'" class="w-4 h-4 text-stone-400 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-width="2" d="M5 15l7-7 7 7"/></svg>
            </button>
            <div x-show="open === 'love'" x-cloak class="pb-4 text-sm text-ink-mute leading-relaxed">{{ product.description_html | striptags | default(product.short_description, true) }}</div>
          </div>
          <div>
            <button @click="open = open === 'ship' ? null : 'ship'" class="w-full flex items-center justify-between py-3.5 text-left">
              <span class="text-[13px] font-medium text-ink-soft">Shipping &amp; Returns</span>
              <svg :class="open === 'ship' ? 'rotate-180' : ''" class="w-4 h-4 text-stone-400 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
            </button>
            <div x-show="open === 'ship'" x-cloak class="pb-4 space-y-1.5">
              {% for l in ['Free shipping on all orders','Delivered in 3–5 business days','7-day easy return','Refund in 5–7 business days'] %}
              <p class="text-sm text-ink-mute flex items-start gap-2"><span class="text-stone-400">&bull;</span>{{ l }}</p>{% endfor %}
              {% if delivery %}<p class="text-sm text-ink-soft mt-2">{{ delivery.label }}</p>{% endif %}
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  {% if related %}
  <section class="mt-14 pt-10 border-t border-stone-100">
    <h2 class="text-center font-serif text-2xl text-ink mb-6">More Choices For You</h2>
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-8">{% for p in related %}{% include "partials/_card.jinja" %}{% endfor %}</div>
  </section>
  {% endif %}

  {% if recently_viewed %}
  <section class="mt-12">
    <h2 class="text-center font-serif text-2xl text-ink mb-6">Recently Viewed</h2>
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-8">{% for p in recently_viewed %}{% include "partials/_card.jinja" %}{% endfor %}</div>
  </section>
  {% endif %}

  {% include "partials/_reviews.jinja" %}
</div>
{% endblock %}
"""

REVIEWS = """<section class="mt-14 pt-10 border-t border-stone-100" id="reviews">
  <h2 class="text-center font-serif text-2xl text-ink mb-6">Customer Reviews</h2>
  <div class="max-w-lg mx-auto">
    {% if reviews and reviews.total %}
    <div class="flex items-center gap-8 mb-6">
      <div class="text-center flex-shrink-0">
        <p class="text-4xl font-bold text-ink leading-none">{{ reviews.average | round(2) }}</p>
        <p class="text-xs text-stone-400 mt-0.5">out of 5</p>
        <div class="flex justify-center gap-0.5 mt-1.5">""" + STAR * 5 + """</div>
        <p class="text-xs text-stone-400 mt-1">{{ reviews.total }} reviews</p>
      </div>
      <div class="flex-1 space-y-1.5">
        {% for b in reviews.breakdown %}
        <div class="flex items-center gap-2">
          <span class="text-xs text-stone-400 w-3 text-right">{{ b.stars }}</span>
          <div class="flex-1 bg-stone-100 rounded-full h-1.5 overflow-hidden"><div class="bg-amber-400 h-1.5 rounded-full" style="width:{{ b.pct }}%"></div></div>
          <span class="text-xs text-stone-400 w-6">{{ b.count }}</span>
        </div>
        {% endfor %}
      </div>
    </div>
    <div class="space-y-6 pt-4 border-t border-stone-100">
      {% for r in reviews.items %}
      <div>
        <div class="flex gap-0.5 mb-1">{% for i in range(r.rating) %}""" + STAR + """{% endfor %}</div>
        <p class="text-sm font-semibold text-ink">{{ r.title or 'Review' }}</p>
        <p class="text-sm text-ink-mute leading-relaxed mt-1">{{ r.body }}</p>
        <p class="text-xs text-stone-400 mt-1">{{ r.author }}</p>
      </div>
      {% endfor %}
    </div>
    {% else %}
    <div class="flex flex-col items-center gap-2">
      <div class="flex gap-0.5">""" + STAR.replace('text-amber-400', 'text-stone-300') * 5 + """</div>
      <p class="text-sm text-stone-500">Be the first to write a review</p>
    </div>
    {% endif %}
  </div>
</section>
"""

QUICKVIEW = """<div class="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4" onclick="this.remove()">
  <div class="bg-white max-w-md w-full p-6 rounded" onclick="event.stopPropagation()">
    <h2 class="font-serif text-xl text-ink">{{ product.title }}</h2>
    <p class="text-lg font-bold text-ink mt-2">{{ product.current_price | money }}</p>
    <p class="text-sm text-ink-mute mt-2">{{ product.short_description }}</p>
    <a href="{{ product.url }}" class="mt-4 block w-full text-center bg-ink text-white text-sm font-semibold py-3 rounded">View details</a>
  </div>
</div>
"""

CART = """{% extends "base.jinja" %}
{% block content %}
<div class="max-w-4xl mx-auto px-4 sm:px-6 py-8">
  <nav class="flex items-center gap-2 text-xs text-stone-400 mb-6">
    <a href="{{ url('shopfront:home') }}" class="hover:text-ink-soft">Home</a><span>&rsaquo;</span><span class="text-ink-soft font-medium">Cart</span>
  </nav>
  <div class="bg-emerald-50 border border-emerald-100 rounded-2xl p-4 mb-6">
    <p class="text-xs font-medium text-emerald-800">You're eligible for <strong>FREE Shipping</strong> — all orders ship free.</p>
    <div class="w-full bg-emerald-200 rounded-full h-1.5 mt-2"><div class="bg-emerald-500 h-1.5 rounded-full" style="width:100%"></div></div>
  </div>
  {% include "partials/_cart_page.jinja" %}
</div>
{% endblock %}
"""

CART_PAGE = """<div id="cart-page">
{% if cart and cart.items %}
<div class="divide-y divide-stone-100 border-y border-stone-100">
  {% for it in cart.items %}
  <div class="flex gap-4 py-4">
    <a href="{{ it.url }}" class="w-20 h-20 rounded-lg overflow-hidden bg-sand flex-shrink-0">
      {% if it.image_url %}<img src="{{ it.image_url }}" alt="{{ it.title }}" class="w-full h-full object-cover">{% endif %}
    </a>
    <div class="flex-1 min-w-0">
      <a href="{{ it.url }}" class="text-sm font-medium text-ink line-clamp-2 hover:underline">{{ it.title }}</a>
      {% if it.variant_label %}<p class="text-xs text-stone-400 mt-0.5">{{ it.variant_label }}</p>{% endif %}
      <form hx-post="{{ url('shopfront:cart_update') }}" hx-target="#cart-page" hx-swap="outerHTML" hx-select="#cart-page" class="flex gap-2 mt-2 max-w-[160px]">
        {{ csrf_input }}<input type="hidden" name="item" value="{{ it.item_id }}"><input type="hidden" name="view" value="page">
        <input name="quantity" value="{{ it.quantity }}" class="w-16 border border-stone-200 rounded px-2 py-1 text-sm">
        <button class="text-xs border border-stone-200 rounded px-2 hover:border-ink">Update</button>
      </form>
    </div>
    <div class="text-sm font-semibold text-ink">{{ it.line_total | money }}</div>
  </div>
  {% endfor %}
</div>
<div class="flex items-center justify-between mt-6">
  <span class="text-sm text-ink-mute">{% if cart.free_ship_remaining %}Add {{ cart.free_ship_remaining | money }} for free shipping{% else %}Subtotal{% endif %}</span>
  <span class="text-xl font-bold text-ink">{{ cart.subtotal | money }}</span>
</div>
<a href="{{ url('shopfront:checkout') }}" class="mt-4 block w-full text-center bg-ink text-white py-3.5 rounded-xl text-sm font-semibold hover:bg-ink-soft transition-colors">Secure Checkout</a>
{% else %}
<div class="text-center py-16">
  <p class="text-ink-mute text-sm mb-4">Your cart is empty.</p>
  <a href="{{ url('shopfront:shop') }}" class="text-sm font-semibold text-ink underline">Continue shopping</a>
</div>
{% endif %}
</div>
"""

CART_DRAWER = """<div id="cart-drawer-body" class="flex flex-col h-full">
  <div class="flex-1 overflow-y-auto">
    {% if cart and cart.items %}
    <ul class="divide-y divide-stone-100">
      {% for it in cart.items %}
      <li class="flex gap-3 p-4">
        <a href="{{ it.url }}" class="w-16 h-16 rounded-lg overflow-hidden bg-sand flex-shrink-0">
          {% if it.image_url %}<img src="{{ it.image_url }}" alt="{{ it.title }}" class="w-full h-full object-cover">{% endif %}
        </a>
        <div class="flex-1 min-w-0">
          <a href="{{ it.url }}" class="text-sm font-medium text-ink line-clamp-2">{{ it.title }}</a>
          <p class="text-xs text-stone-400 mt-0.5">{% if it.variant_label %}{{ it.variant_label }} &middot; {% endif %}Qty {{ it.quantity }}</p>
          <p class="text-sm font-semibold text-ink mt-1">{{ it.line_total | money }}</p>
        </div>
      </li>
      {% endfor %}
    </ul>
    {% else %}
    <div class="flex items-center justify-center p-8 text-center h-full min-h-[240px]">
      <p class="text-stone-500 text-sm">Your cart is empty</p>
    </div>
    {% endif %}
  </div>
  <div class="p-4 border-t border-stone-100 space-y-3 bg-white">
    <div class="flex justify-between text-sm"><span class="text-ink-mute">Total</span><span class="font-semibold">{{ cart.subtotal | money if cart else 0 }}</span></div>
    {% if cart and cart.items %}
    <a href="{{ url('shopfront:checkout') }}" class="block w-full text-center bg-ink text-white py-3 rounded-xl text-sm font-medium hover:bg-ink-soft">Secure Checkout</a>
    <a href="{{ url('shopfront:cart') }}" class="block w-full text-center text-sm text-ink-mute hover:text-ink underline">View Cart</a>
    {% else %}
    <a href="{{ url('shopfront:shop') }}" class="block w-full text-center bg-ink text-white py-3 rounded-xl text-sm font-medium">Continue Shopping</a>
    {% endif %}
  </div>
</div>
"""

CART_FRAG = '<span class="absolute -top-0.5 -right-0.5 bg-ink text-white text-[9px] rounded-full min-w-[15px] h-4 px-0.5 flex items-center justify-center font-semibold" id="cart-count">{{ cart.item_count if cart else 0 }}</span>\n'

CHECKOUT = """{% extends "base.jinja" %}
{% block content %}
<div class="max-w-2xl mx-auto px-4 sm:px-6 py-10">
  <h1 class="font-serif text-3xl text-ink mb-6">Checkout</h1>
  <form method="post" action="{{ url('shopfront:checkout') }}" class="space-y-3">
    {{ csrf_input }}
    <input name="email" type="email" placeholder="Email" required class="w-full border border-stone-200 rounded px-3 py-2.5 text-sm">
    <input name="name" placeholder="Full name" required class="w-full border border-stone-200 rounded px-3 py-2.5 text-sm">
    <input name="line1" placeholder="Address" required class="w-full border border-stone-200 rounded px-3 py-2.5 text-sm">
    <div class="flex gap-3">
      <input name="city" placeholder="City" class="w-full border border-stone-200 rounded px-3 py-2.5 text-sm">
      <input name="state" placeholder="State" class="w-full border border-stone-200 rounded px-3 py-2.5 text-sm">
      <input name="postal_code" placeholder="PIN" class="w-full border border-stone-200 rounded px-3 py-2.5 text-sm">
    </div>
    <input name="phone" placeholder="Phone" class="w-full border border-stone-200 rounded px-3 py-2.5 text-sm">
    <div id="shipping-methods">{% include "partials/_shipping_methods.jinja" %}</div>
    <div id="checkout-summary">{% include "partials/_checkout_summary.jinja" %}</div>
    <label class="flex items-center gap-2 text-sm text-ink-soft"><input type="radio" name="payment_method" value="cod" checked> Cash on delivery</label>
    <button class="w-full bg-ink text-white py-3.5 rounded-xl text-sm font-semibold hover:bg-ink-soft">Place order</button>
  </form>
</div>
{% endblock %}
"""

SHIP = """{% if shipping_methods %}
<div class="space-y-1">
  {% for m in shipping_methods %}
  <label class="flex items-center gap-2 text-sm text-ink-soft"><input type="radio" name="shipping_method" value="{{ m.id }}"> {{ m.label }} &mdash; {{ m.price | money }} <span class="text-stone-400">({{ m.eta_label }})</span></label>
  {% endfor %}
</div>
{% else %}<p class="text-xs text-stone-400">Enter address for shipping options.</p>{% endif %}
"""

SUMMARY = """<div class="border-y border-stone-100 py-3">
  <div class="flex justify-between text-sm font-medium"><span>Subtotal</span><span>{{ cart.subtotal | money if cart else 0 }}</span></div>
  {% if coupon and coupon.message %}<p class="text-xs text-emerald-600 mt-1">{{ coupon.message }}</p>{% endif %}
</div>
"""

ACCOUNT = """{% extends "base.jinja" %}
{% block content %}
<div class="max-w-2xl mx-auto px-4 sm:px-6 py-10">
{% if customer.is_authenticated %}
  <div class="flex items-center justify-between mb-6">
    <h1 class="font-serif text-3xl text-ink">Hi, {{ customer.name }}</h1>
    <form method="post" action="{{ url('shopfront:logout') }}">{{ csrf_input }}<button class="text-xs border border-stone-200 rounded px-3 py-1.5 hover:border-ink">Sign out</button></form>
  </div>
  <h2 class="text-sm font-semibold text-ink mb-2">Orders</h2>
  {% for o in customer.orders %}
  <a href="{{ o.url }}" class="flex justify-between border-t border-stone-100 py-3 text-sm">
    <span>{{ o.number }} &middot; {{ o.status_label }}</span><span>{{ o.total | money }}</span>
  </a>
  {% else %}<p class="text-ink-mute text-sm">No orders yet.</p>{% endfor %}
{% else %}
  <h1 class="font-serif text-3xl text-ink mb-6">Sign in</h1>
  <form method="post" action="{{ url('shopfront:login') }}" class="space-y-3 max-w-sm">
    {{ csrf_input }}
    <input name="email" type="email" placeholder="Email" class="w-full border border-stone-200 rounded px-3 py-2.5 text-sm">
    <input name="password" type="password" placeholder="Password" class="w-full border border-stone-200 rounded px-3 py-2.5 text-sm">
    <button class="w-full bg-ink text-white py-3 rounded-xl text-sm font-semibold">Sign in</button>
  </form>
  <h2 class="text-sm font-semibold text-ink mt-8 mb-3">New customer</h2>
  <form method="post" action="{{ url('shopfront:register') }}" class="space-y-3 max-w-sm">
    {{ csrf_input }}
    <input name="first_name" placeholder="First name" class="w-full border border-stone-200 rounded px-3 py-2.5 text-sm">
    <input name="email" type="email" placeholder="Email" class="w-full border border-stone-200 rounded px-3 py-2.5 text-sm">
    <input name="password" type="password" placeholder="Password" class="w-full border border-stone-200 rounded px-3 py-2.5 text-sm">
    <button class="w-full border border-ink text-ink py-3 rounded-xl text-sm font-semibold hover:bg-ink hover:text-white">Create account</button>
  </form>
{% endif %}
</div>
{% endblock %}
"""

WISHLIST = """{% extends "base.jinja" %}
{% block content %}
<div class="max-w-7xl mx-auto px-4 sm:px-6 py-10">
  <h1 class="font-serif text-3xl text-ink mb-6">Wishlist</h1>
  {% if wishlist %}<div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-x-4 gap-y-8">{% for p in wishlist %}{% include "partials/_card.jinja" %}{% endfor %}</div>
  {% else %}<p class="text-ink-mute text-sm">Nothing saved yet.</p>{% endif %}
</div>
{% endblock %}
"""

WISHBTN = """<button hx-post="{{ url('shopfront:wishlist_toggle') }}" hx-vals='{"product":"{{ wl_slug }}"}' hx-swap="outerHTML"
        class="p-2 rounded-full border border-stone-200 text-stone-400 hover:text-red-500 hover:border-red-200">
  <svg class="w-5 h-5" fill="{{ 'currentColor' if wl_active else 'none' }}" stroke="currentColor" viewBox="0 0 24 24"><path stroke-width="1.5" d="M4.3 6.3a4.5 4.5 0 000 6.4L12 20.4l7.7-7.7a4.5 4.5 0 00-6.4-6.4L12 7.6l-1.3-1.3a4.5 4.5 0 00-6.4 0z"/></svg>
</button>
"""

ORDER = """{% extends "base.jinja" %}
{% block content %}
<div class="max-w-2xl mx-auto px-4 sm:px-6 py-10">
{% if order %}
  <h1 class="font-serif text-3xl text-ink">Order {{ order.number }}</h1>
  <p class="text-ink-mute text-sm mt-1">{{ order.status_label }}</p>
  <table class="w-full text-sm mt-6">
    {% for i in order.items %}<tr class="border-b border-stone-100"><td class="py-2">{{ i.title }} &times;{{ i.quantity }}</td><td class="py-2 text-right">{{ i.line_total | money }}</td></tr>{% endfor %}
    <tr class="border-b border-stone-100"><td class="py-2">Shipping</td><td class="py-2 text-right">{{ order.shipping_total | money }}</td></tr>
    <tr><td class="py-3 font-semibold">Total</td><td class="py-3 text-right font-bold text-lg">{{ order.grand_total | money }}</td></tr>
  </table>
  {% if order.shipping_address %}<p class="text-sm text-ink-mute mt-4">Ship to {{ order.shipping_address.name }}, {{ order.shipping_address.line1 }}, {{ order.shipping_address.city }}</p>{% endif %}
{% else %}<p>Order not found.</p>{% endif %}
</div>
{% endblock %}
"""

TRACK = """{% extends "base.jinja" %}
{% block content %}
<div class="max-w-md mx-auto px-4 sm:px-6 py-10">
  <h1 class="font-serif text-3xl text-ink mb-6">Track order</h1>
  <form method="post" action="{{ url('shopfront:track') }}" class="space-y-3">
    {{ csrf_input }}
    <input name="number" placeholder="Order number" value="{{ track_number }}" class="w-full border border-stone-200 rounded px-3 py-2.5 text-sm">
    <input name="email" type="email" placeholder="Email" value="{{ track_email }}" class="w-full border border-stone-200 rounded px-3 py-2.5 text-sm">
    <button class="w-full bg-ink text-white py-3 rounded-xl text-sm font-semibold">Track</button>
  </form>
  {% if tracked %}
    {% if order %}<div class="mt-6 border-t border-stone-100 pt-4"><strong>{{ order.number }}</strong> &mdash; {{ order.status_label }}
      {% if order.tracking %}<p class="text-sm text-ink-mute mt-1">{{ order.tracking.carrier }} {{ order.tracking.number }}</p>{% endif %}
    </div>{% else %}<p class="mt-4 text-sm text-red-500">No match found.</p>{% endif %}
  {% endif %}
</div>
{% endblock %}
"""

PAGE = """{% extends "base.jinja" %}
{% block title %}{{ page.title }} &middot; {{ store.name }}{% endblock %}
{% block content %}
<div class="max-w-3xl mx-auto px-4 sm:px-6 py-12">
  <h1 class="font-serif text-3xl sm:text-4xl text-ink mb-6">{{ page.title }}</h1>
  <div class="text-[15px] leading-relaxed text-ink-soft prose">{{ page.body_html | safe }}</div>
</div>
{% endblock %}
"""

NOT_FOUND = """{% extends "base.jinja" %}
{% block content %}
<div class="max-w-md mx-auto px-4 py-32 text-center">
  <p class="font-serif text-6xl text-ink">404</p>
  <p class="text-ink-mute text-sm mt-3 mb-6">This page could not be found.</p>
  <a href="{{ url('shopfront:home') }}" class="inline-block bg-ink text-white text-sm font-semibold px-6 py-3 rounded">Back home</a>
</div>
{% endblock %}
"""

SUGGEST = """<div id="search-suggest" class="bg-white border border-stone-200 rounded shadow-lg">
  {% for p in suggestions %}<a href="{{ p.url }}" class="flex items-center gap-3 px-3 py-2 hover:bg-sand text-sm">
    {% if p.images %}<img src="{{ p.images[0].url }}" class="w-8 h-8 object-cover rounded" alt="">{% endif %}
    <span class="flex-1">{{ p.title }}</span><span class="text-ink-mute">{{ p.current_price | money }}</span>
  </a>{% endfor %}
</div>
"""

FILES = {
    "base.jinja": BASE, "home.jinja": HOME, "shop.jinja": SHOP, "product.jinja": PRODUCT,
    "cart.jinja": CART, "checkout.jinja": CHECKOUT, "account.jinja": ACCOUNT,
    "wishlist.jinja": WISHLIST, "order.jinja": ORDER, "track.jinja": TRACK,
    "page.jinja": PAGE, "not_found.jinja": NOT_FOUND,
    "partials/_card.jinja": CARD, "partials/_grid.jinja": GRID,
    "partials/_cart_drawer.jinja": CART_DRAWER, "partials/_cart_page.jinja": CART_PAGE,
    "partials/_cart_fragments.jinja": CART_FRAG, "partials/_checkout_summary.jinja": SUMMARY,
    "partials/_reviews.jinja": REVIEWS, "partials/_quickview.jinja": QUICKVIEW,
    "partials/_shipping_methods.jinja": SHIP, "partials/_suggest.jinja": SUGGEST,
    "partials/_wishlist_btn.jinja": WISHBTN,
}


class Command(BaseCommand):
    help = "Seed the demo 'jwdemo' skin (Diamo/sd-jw-demo1 clone) and optionally activate it."

    def add_arguments(self, parser):
        parser.add_argument("--project", default="acme-store")
        parser.add_argument("--activate", action="store_true")

    def handle(self, *args, **opts):
        try:
            project = Project.objects.get(slug=opts["project"])
        except Project.DoesNotExist:
            raise CommandError(f"No project with slug {opts['project']!r}.")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("jwdemo/theme.json", json.dumps(
                {"name": "jwdemo", "version": "2.0", "author": "SD demo (Diamo clone)"}))
            for path, content in FILES.items():
                z.writestr(f"jwdemo/{path}", content)
        buf.seek(0)

        Skin.objects.filter(
            slug__in=[f"{project.slug}-luma", f"{project.slug}-jwdemo"],
            source="upload",
        ).delete()
        owner = (
            project.memberships.filter(role="owner", is_active=True)
            .select_related("user").first()
        )
        skin = create_skin_from_upload(
            project=project, user=owner.user if owner else None,
            fileobj=buf, label="jwdemo",
        )
        skin.status = SkinStatus.APPROVED
        skin.description = "Clone of the sd-jw-demo1 (Diamo) jewellery storefront — Cormorant + Manrope, warm neutral palette."
        skin.save(update_fields=["status", "description", "updated_at"])
        self.stdout.write(self.style.SUCCESS(
            f"Seeded '{skin.slug}' ({skin.files.count()} templates), status={skin.status}."
        ))

        if opts["activate"]:
            ts, _ = ThemeSettings.objects.get_or_create(project=project)
            ts.skin = skin
            ts.save(update_fields=["skin", "updated_at"])
            self.stdout.write(self.style.SUCCESS(f"Activated on {project.name}."))
