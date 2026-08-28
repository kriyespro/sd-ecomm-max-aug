# Storefront skins

Each subfolder here is a **skin** — a full or partial override of the
server-rendered storefront (`/app/`, app `apps.shopfront`).

```
templates/shopfront/skins/
  default/            # the built-in skin — every store falls back to this
    base.jinja
    home.jinja
    product.jinja
    ...
    partials/
  aurora/             # a second skin
    home.jinja        # only the files you want to change
    base.jinja
```

## How resolution works

Views render logical names like `shopfront/home.jinja`. A request-scoped skin
(set by `StorefrontSkinMiddleware` from the store's `ThemeSettings.skin`) rewrites
the lookup, and the loader tries, in order:

1. `shopfront/skins/<skin>/home.jinja`
2. `shopfront/skins/default/home.jinja`
3. `shopfront/home.jinja` (legacy bare path, normally absent)

So a skin only needs the templates it actually changes; everything else comes
from `default/`. `{% extends "shopfront/base.jinja" %}` and
`{% include "shopfront/partials/_card.jinja" %}` resolve the same way — override
`base.jinja` in your skin and every page picks it up.

The compiled-template cache key includes the skin, so skins never bleed into each
other across tenants in one process.

## Adding a skin

1. `mkdir templates/shopfront/skins/<slug>/` and add your templates (copy from
   `default/` and edit).
2. Put static assets under `static/skins/<slug>/`.
3. Register the row:
   ```
   python manage.py register_skin <slug> --label "Nice Name"
   ```
4. Deploy.
5. In Mission Control → **Skins**, a platform admin activates it and (optionally,
   per store) grants it under **Skin access**.
6. The store owner picks it on **Theme**, then tunes colours/fonts via
   `ThemeSettings` on top.

## Theme tokens

Colours and fonts come from `ThemeSettings` (`accent`, `font_body`,
`font_heading`, `custom_css`, `tokens` JSON) and are injected into
`base_context`. Read them in your skin's `base.jinja`; keep a sane fallback so
the skin renders even with an empty `ThemeSettings`.
