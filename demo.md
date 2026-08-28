# Demo data & logins

Everything below is created by management commands and is **idempotent** — re-run
to reset. For local dev only.

---

## Accounts

Log in at **`http://127.0.0.1:8000/accounts/login/`** — the **username is the email**.

| role | store | username (email) | password |
|---|---|---|---|
| **Super admin** | — | `admin` | `Superadmin-2026` |
| **Platform Manager** | — | `platmanager@sd.test` | `Platform-mgr-2026` |
| Store Owner | Lumen Lighting | `owner@lumen.test` | `Lumen-owner-2026` |
| Store Manager | Lumen Lighting | `manager@lumen.test` | `Lumen-mgr-2026` |
| Store Owner | Trailhead Outdoors | `owner@trailhead.test` | `Trailhead-owner-2026` |
| Store Manager | Trailhead Outdoors | `manager@trailhead.test` | `Trailhead-mgr-2026` |
| Store Owner | Petal & Vine Florals | `owner@petal.test` | `Petal-owner-2026` |
| Store Manager | Petal & Vine Florals | `manager@petal.test` | `Petal-mgr-2026` |

`seed_demo_accounts` prints this table too. Change the super-admin password with
`manage.py changepassword admin`.

---

## Stores

| store | plan | billing | Mission Control | storefront |
|---|---|---|---|---|
| Acme Store | Starter (trial) | monthly | `/admin/` | `http://acme.localhost:8000/app/` |
| ORNZA | Starter (trial) | monthly | `/admin/` | `http://ornza.localhost:8000/app/` |
| Lumen Lighting | Basic | monthly | `/admin/` | `http://lumen.localhost:8000/app/` |
| Trailhead Outdoors | Growth | yearly | `/admin/` | `http://trailhead.localhost:8000/app/` |
| Petal & Vine Florals | Pro | monthly | `/admin/` | `http://petal.localhost:8000/app/` |

- `*.localhost` resolves to 127.0.0.1 in Chrome automatically. `127.0.0.1:8000` by
  itself won't resolve a store — the Host header must match a store domain.
- Lumen / Trailhead / Petal have **empty catalogs**. Acme has the full demo
  catalogue (`seed_acme`).
- Platform manager `platmanager@sd.test` is the `subscription.manager` on the
  3 seeded stores → earns commission on their invoices.

---

## What each role sees

| screen | super admin | platform manager | store owner / manager | store staff |
|---|---|---|---|---|
| Platform → Stores (create/manage stores) | ✓ | ✓ (only their stores) | — | — |
| Platform → Billing / Skins / Users / impersonate | ✓ | ✗ 403 | — | — |
| Store: Products / Orders / Inventory / Customers | ✓ (in active store) | ✓ (their stores) | ✓ | ✓ |
| Store: Payments / Domains / Team / Plan & billing | ✓ | ✓ | ✓ | ✗ 403 |
| `/admin/` at all | needs `is_staff` | needs `is_staff` | needs `is_staff` | needs `is_staff` — customers get 403 |

Super admin logging in with no store picked sees only the **Platform** nav
section (Stores, Billing, Skins, Users). Pick a store from **Stores → Work on
this store** to load the store section.

---

## Seed commands

```sh
python manage.py seed_acme                 # Acme catalogue (products, images, reviews, coupons)
python manage.py seed_demo_accounts        # 3 stores + owner/manager logins + platform manager
python manage.py seed_skins                # 12 built-in shared skins (mono, noir, ... impact, impact2)
python manage.py seed_ornza_skin           # the "ornza" built-in skin (champagne-gold jewellery)
python manage.py seed_demo_skin --project acme-store --activate   # sandboxed "jwdemo" upload skin on Acme
```

Activate a skin on a store: `manage.py seed_skins --activate <slug> --project <store-slug>`
or in Mission Control → **Theme**. Revert with `--activate default`.

---

## Storefront skins catalogue

16 total: `default` + 13 built-in (`mono noir bloom grove cobalt sunbaked marble
neon linen coral impact impact2 kapiva`) + `ornza` + `acme-store-jwdemo` (Acme's
private sandboxed upload). All render on any store; the owner picks one on the
Theme screen. Origins: `impact` = maroon/gold jewellery (sd-ecomm-impact),
`impact2` = bold Barlow/pill audio (sd-ecomm-impact 2), `kapiva` (label
"Botanica") = cream/forest-green Ayurveda wellness (kapiva-clone), `jwdemo` =
full Diamo clone (sd-jw-demo1), `ornza` = champagne-gold (html-ornza).

---

## Run it

```sh
python manage.py runserver 127.0.0.1:8000
# or the full stack:
cp .env.example .env      # fill DJANGO_SECRET_KEY + POSTGRES_PASSWORD
docker compose up -d --build
```

Health: `/healthz/` (liveness), `/readyz/` (db + cache). See `DEPLOY.md`.
