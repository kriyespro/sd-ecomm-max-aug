# Converting an HTML/CSS/JS theme into an SD storefront skin

An **uploaded skin** is a self-contained bundle of sandboxed Jinja2 templates +
assets that renders the `/app/` storefront for one store. It runs in an
`ImmutableSandboxedEnvironment` with a **curated, read-only data contract** —
no Django ORM objects, no `request`, no `user`, no Python internals. Anything
outside the contract below is unavailable and will fail validation.

---

## PROMPT — paste into an LLM together with your theme files

> You are converting a static HTML/CSS/JS storefront theme into an **SD skin**:
> a bundle of sandboxed Jinja2 templates for the SD headless commerce backend.
> Match the theme's visual design faithfully — same layout, spacing, type,
> colour, components — but drive all content from the data contract below.
>
> ### Output — a folder `skins/<slug>/` containing exactly:
>
> ```
> base.jinja            product.jinja      account.jinja     page.jinja
> home.jinja            cart.jinja         wishlist.jinja     not_found.jinja
> shop.jinja            checkout.jinja     order.jinja        track.jinja
> partials/
>   _card.jinja              _cart_drawer.jinja      _cart_fragments.jinja
>   _grid.jinja              _cart_page.jinja        _checkout_summary.jinja
>   _reviews.jinja           _quickview.jinja        _shipping_methods.jinja
>   _suggest.jinja           _wishlist_btn.jinja
> assets/
>   css/…  js/…  images/…  fonts/…
> theme.json            # {"name": "...", "version": "1.0", "author": "..."}
> ```
>
> Every listed template is **required**. Produce the full contents of each file,
> then a zip of the folder.
>
> ### Rules
>
> 1. **Layout**: every page starts `{% extends "base.jinja" %}` and fills
>    `{% block title %}`, `{% block head %}`, `{% block content %}`,
>    `{% block scripts %}`. Reference siblings by bare path:
>    `{% include "partials/_card.jinja" %}`.
> 2. **Data**: use only the objects/fields in *Data contract*. They are
>    read-only dicts. No method calls. **Forbidden and auto-rejected**: `__…`,
>    `self`, `config`, `request`, `lipsum`, `cycler`, `namespace`, `import`,
>    `{% include %}`/`{% extends %}` of anything outside `skins/<slug>/`,
>    `{% set %}` used to reach attributes, any filter not in *Filters*.
> 3. **Links**: `{{ url('shopfront:product', slug=p.slug) }}`. Never hardcode
>    `/app/...`. Allowed names: `home shop product quickview search_suggest
>    cart cart_add cart_update cart_remove cart_drawer checkout shipping_quote
>    coupon_preview order account login register logout wishlist
>    wishlist_toggle track page review`.
> 4. **Assets**: `{{ asset('css/app.css') }}`, `{{ asset('images/logo.svg') }}`
>    — resolves to this skin's asset dir. Inline nothing over ~2 KB; put it in
>    `assets/`. External hosts allowed **only** for web fonts.
> 5. **Money**: `{{ p.price | money }}` (never format prices yourself).
> 6. **Forms**: every `<form method="post">` and every `hx-post` form contains
>    `{{ csrf_input }}`.
> 7. **Dynamic behaviour**: use the HTMX endpoints in *Interactivity*. Keep the
>    theme's own JS only for visual things (sliders, menus, parallax). Preserve
>    the DOM ids/targets listed there — the backend swaps into them.
> 8. **Accent colour**: wire the theme's primary/CTA colour to `{{ accent }}`
>    (a hex string) so the store owner's Theme setting takes effect. Other
>    tokens: `{{ font_body }}`, `{{ font_heading }}`, `{{ custom_css }}` (inject
>    raw in `<head>` — it is already sanitised server-side).
> 9. No analytics/tracking/pixels unless fed through `store.tracking`.
>
> ### Process
>
> 1. Map theme pages → skin templates (index→home, product→product,
>    collection/category→shop, cart→cart, etc.). Build `not_found.jinja` from
>    the theme's 404 or a stripped layout.
> 2. Replace hardcoded product/collection markup with loops over the contract.
> 3. Replace nav/menu/footer with `store.menu` / `store.footer_links` + `url()`.
> 4. Factor repeated markup into the `partials/`. `_card.jinja` expects `p`.
> 5. Move CSS/JS/img/fonts into `assets/`, rewrite every reference to
>    `{{ asset(...) }}`.
> 6. Self-check against the *Forbidden* list before returning.
>
> ---
>
> ### Data contract (curated — available in every template)
>
> **`store`** — `name`, `currency` (`"₹"`), `logo_url`, `accent`,
> `font_body`, `font_heading`, `custom_css`, `menu` (`[{label, url, children:[…]}]`),
> `footer_links` (`[{title, url}]`), `announcement` (`{text, url}` or none),
> `hero` (`{heading, subheading, image_url, cta_label, cta_url}` or none),
> `social` (`{instagram, facebook, …}`), `tracking` (`{ga4, meta_pixel}` or empty).
>
> **`cart`** — `item_count`, `subtotal`, `currency`, `free_ship_over`,
> `free_ship_remaining`, `items`: `[{item_id, title, url, image_url,
> variant_label, unit_price, quantity, line_total, slug}]`.
>
> **`customer`** — `is_authenticated`, `name`, `email`,
> `orders` `[{number, url, placed_at, status, status_label, total, item_count}]`,
> `wishlist_slugs` (list).
>
> **`p` / `product`** (product card & PDP) — `title`, `slug`, `url`, `sku`,
> `price`, `current_price`, `compare_at_price`, `on_sale`, `discount_pct`,
> `in_stock`, `available_qty`, `is_new_arrival`, `short_description`,
> `description_html`, `brand` (`{name}` or none), `category` (`{name, slug, url}`
> or none), `images` `[{url, alt}]`, `options` `[{name, values:[…]}]`,
> `variants` `[{id, label, price, current_price, in_stock, options:{Name:Value}}]`,
> `rating_avg`, `rating_count`.
>
> **Listing pages** (`shop.jinja`, `_grid.jinja`) — `products` `[product]`,
> `pagination` `{page, pages, count, has_prev, has_next, prev_url, next_url}`,
> `filters` `{categories:[{name, slug, url, count}], sorts:[{key, label,
> selected}], active_category, query, price_min, price_max}`.
>
> **`home.jinja`** — `featured` `[product]`, `new_arrivals` `[product]`,
> `category_tiles` `[{name, url, image_url}]`, `testimonials`
> `[{author, body, rating}]` (plus `store.hero`).
>
> **PDP extras** (`product.jinja`) — `reviews` `{average, total,
> breakdown:[{stars, count, pct}], items:[{author, rating, title, body,
> created_at}], can_submit}`, `related` `[product]`, `recently_viewed`
> `[product]`, `delivery` `{min_date, max_date, label, free_over}` (or none).
>
> **`order`** (`order.jinja`, `track.jinja`) — `number`, `status`,
> `status_label`, `placed_at`, `items` `[{title, quantity, unit_price,
> line_total, image_url}]`, `subtotal`, `shipping_total`, `discount_total`,
> `grand_total`, `shipping_address` `{name, line1, line2, city, state,
> postal_code, country, phone}`, `tracking` `{carrier, number, url,
> events:[{at, status, note}]}` (or none). `track.jinja` also: `tracked`
> (bool), `track_number`, `track_email`.
>
> **`page`** (`page.jinja`) — `title`, `body_html`, `updated_at`.
>
> **`checkout.jinja`** — `cart`, `shipping_methods` `[{id, label, price,
> eta_label, selected}]`, `coupon` `{code, ok, message, discount}`,
> `countries` `[{code, name}]`.
>
> ### Filters
>
> `money`, `date` (`{{ d | date('%d %b %Y') }}`), `truncate`, `length`,
> `default`, `join`, `lower`, `upper`, `title`, `trim`, `striptags`,
> `urlencode`, `tojson`, `int`, `float`, `round`, `abs`, `first`, `last`,
> `reverse`, `sort`, `selectattr`, `map`, `list`, `safe` (use sparingly).
>
> ### Interactivity (HTMX — keep these targets)
>
> | Action | Method / URL | Body fields | hx-target |
> |---|---|---|---|
> | Add to cart | POST `url('shopfront:cart_add')` | `product` (slug), `variant` (id, optional), `quantity` | `hx-swap="none"`; server fires `cart-open` + refreshes `#cart-count` |
> | Cart qty | POST `url('shopfront:cart_update')` | `item`, `quantity`, `view` (`drawer`\|`page`) | the cart container |
> | Cart remove | POST `url('shopfront:cart_remove')` | `item`, `view` | the cart container |
> | Cart drawer | GET `url('shopfront:cart_drawer')` | — | `#cart-drawer-body` |
> | Quick view | GET `url('shopfront:quickview', slug=p.slug)` | — | `#quickview-body`, then `$dispatch('open-quick')` |
> | Search suggest | GET `url('shopfront:search_suggest')` | `?q=` | `#search-suggest` |
> | Wishlist toggle | POST `url('shopfront:wishlist_toggle')` | `product` (slug) | the button, swap `outerHTML` |
> | Coupon preview | POST `url('shopfront:coupon_preview')` | `coupon_code` | `#checkout-summary` |
> | Shipping quote | POST `url('shopfront:shipping_quote')` | `name line1 line2 city state postal_code country phone payment_method` | `#shipping-methods` |
> | Review submit | POST `url('shopfront:review', slug=product.slug)` | `author_name author_email rating title body` | `#reviews` |
>
> Required DOM hooks anywhere they apply: `#cart-count`, `#cart-drawer-body`,
> `#quickview-body`, `#search-suggest`, `#checkout-summary`, `#shipping-methods`,
> `#reviews`. Load HTMX and Alpine yourself in `base.jinja` `{% block head %}`
> (bundle them in `assets/js/` — no CDN except fonts).

---

## After conversion

1. Zip the `skins/<slug>/` folder.
2. Mission Control → **Skins → Upload** (store owner or manager). Validation
   runs on upload; fix anything it flags.
3. A platform admin reviews and approves.
4. Pick it on the **Theme** screen. Tune colours/fonts on top.
