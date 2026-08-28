# SD Commerce — headless multi-store backend

Django 5.2 · DRF · Jinja2 storefronts · Celery · Postgres · Redis.
One install serves many stores; each store resolves from the request host.

## Local

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py seed_acme            # demo catalogue
python manage.py seed_skins           # 13 built-in storefront skins
python manage.py seed_demo_accounts   # 3 demo stores + logins
python manage.py runserver
```

Storefront: `http://acme.localhost:8000/app/` · Mission Control: `/admin/` ·
Django admin: `/sd/` · API: `/api/v1/` · health: `/healthz/` `/readyz/`.

Demo logins & stores → **[demo.md](demo.md)**.

## Production (Docker)

```sh
cp .env.prod .env        # secrets are pre-generated in .env.prod
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Full guide, Cloudflare per-domain setup, backups, scaling → **[DEPLOY.md](DEPLOY.md)**.

## Layout

- `apps/` — core, accounts, projects, billing, control (Mission Control),
  catalog, inventory, cart, orders, checkout, payments, shipping, customers,
  coupons, reviews, wishlist, cms, seo, notifications, webhooks, media,
  analytics, api, shopfront/storefront.
- `config/settings/` — `base` → `development` / `production`.
- `templates/shopfront/skins/` — swappable storefront themes.
