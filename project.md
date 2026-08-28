# Project — SD Headless Commerce Platform

A reusable, multi-project (multi-store) e-commerce backend built on Django. One backend, many frontends. Business data is scoped by a tenant/project ID rather than running a separate Django install per store. Frontends (HTMX stores, React/Next, mobile) consume the same REST API.

> Source: architecture from `plan.md`, tech stack + engineering rules from `Rule.md`.

---

## 0. Role & Goal

You are a senior Django SaaS architect and engineer.

- Build a scalable, revenue-focused, headless commerce engine + management platform.
- Deliver MVP fast, then iterate.
- Write clean, modular, production-ready code.
- Before building anything ask: **does this help the store earn or save money?** YES → build. NO → delay.

**One architectural rule:** the storefront frontend is **not** part of this backend. This repo is a headless commerce engine plus an admin/management panel. Every new store becomes: create project → configure domain/theme/payment/shipping → add products → connect a frontend → launch.

---

## 1. Tech Stack

| Layer | Choice |
|---|---|
| Language | Python 3.14 |
| Framework | Django 5.2 LTS |
| API | Django REST Framework, `/api/v1/`, versioned |
| Admin template engine | Jinja2 (primary, `.jinja` extension) |
| Django Templates | ONLY for Django's built-in admin at `/sd/` |
| Interactivity | HTMX (server-driven), Alpine.js (UI toggles only) |
| CSS | Tailwind CSS (utility-first) |
| DB | SQLite (dev) → PostgreSQL (prod) |
| Cache / queue | Redis |
| Async jobs | Celery + Celery beat |
| Auth | Django session auth (admin) + token/JWT (API) |
| Storage | local / Docker volume (dev) → S3 / Cloudflare R2 (prod) |
| Infra | Docker, Nginx, Postgres, Redis, Celery worker + beat, object storage |

---

## 2. Session Start Rules (MANDATORY)

Before doing anything in a new session:

1. Read `dev.txt` — current progress and phase.
2. Read `dev_plan.txt` — which phase you are in.
3. Never assume context — check these files first.
4. After completing any task, update `dev.txt` with what was done.

Dev file formats:

- `dev_plan.txt` — step-by-step phases for the full project.
- `dev.txt` — updated after every completed task:
  ```
  [DONE] Phase 1 - Models created: Project, Account, Membership
  [DONE] Phase 1 - Migrations applied
  [IN PROGRESS] Phase 2 - Catalog models
  ```
- `test_user.txt` — always create test credentials:
  ```
  email: test@example.com
  password: test1234
  ```

---

## 3. Project Structure

```
manage.py
durga.py                  ← dev runner + cache clear
requirements.txt
project.md                ← this file
Rule.md                   ← original engineering rules
plan.md                   ← original architecture notes
dev_plan.txt              ← phased execution plan
dev.txt                   ← progress tracker (update after every task)
test_user.txt             ← test credentials

config/
  settings/
    base.py
    development.py
    production.py
  urls.py
  asgi.py
  wsgi.py

apps/
  core/                   ← shared base classes, middleware, mixins
  accounts/               ← auth, profile, platform + store roles
  projects/               ← Project (store/tenant) model, domain mapping, settings
  catalog/                ← products, product types, variants, attributes, brands
  categories/             ← nested categories
  inventory/              ← stock, warehouses, stock movements, alerts
  cart/
  checkout/
  orders/                 ← orders, items, statuses, notes
  payments/               ← pluggable providers (Razorpay, Stripe, PayU, COD)
  shipping/               ← zones, methods, courier abstraction, tracking
  customers/              ← customer profiles, groups, segmentation
  coupons/                ← discount engine
  reviews/
  wishlist/
  cms/                    ← pages, banners, content blocks, FAQs
  marketing/
  notifications/          ← email/SMS/WhatsApp provider abstraction
  analytics/              ← dashboard metrics, reports
  search/
  media/                  ← upload, thumbnails, WebP/AVIF, storage abstraction
  seo/
  webhooks/
  audit/                  ← audit log of who/what/when/which project

templates/
  base.jinja
  layouts/
  components/             ← button.jinja, card.jinja, input.jinja
  partials/               ← _*.jinja for HTMX responses
  control/                ← Mission Control admin (see §6)
    base_control.jinja
    dashboard.jinja
    ...
  pages/

static/
```

Rules:

- Each feature = separate Django app under `apps/`.
- Avoid one giant `shop` app.
- Avoid hardcoding — use settings/config.
- Business logic in `services.py`, never in views or templates.

---

## 4. Multi-Project / Multi-Store Core

The foundation. A `Project` (aka Store) owns all business data via a `project` FK.

```
Project
 ├── Products
 ├── Categories
 ├── Customers
 ├── Orders
 ├── Coupons
 ├── Pages / Banners
 ├── Payments
 └── Settings
```

`Project` fields: name, logo, favicon, primary domain, custom domain mapping, status (Active / Suspended / Draft), timezone, currency, country/state, tax config, branding, email settings, payment config, shipping config, SEO settings, notification settings, feature toggles, store admin users.

### Multi-domain resolution

```
Request  Host: store-a.com
   ↓
Django middleware (apps/core)
   ↓
Find Project by domain (primary or custom mapping)
   ↓
Attach request.project
   ↓
All queries filtered by request.project
```

**Security:** never trust a `project_id` sent by the frontend for tenant isolation. Resolve the project from authenticated context or the request domain and enforce it server-side.

---

## 5. Roles & Permissions

### Platform-level roles
Super Admin, Platform Owner, Platform Manager.

### Store-level roles
Store Owner, Store Manager, Staff, Customer.

### Permissions
Use Django's permission system plus a **project/store-level permission layer**. Granular perms: view/create/edit/delete products, manage inventory, view/update/refund orders, manage customers, coupons, pages, banners, shipping, payments, reports, store settings, staff.

### Platform Admin vs Store Admin

| Platform Admin | Store Admin |
|---|---|
| All projects, all users, plans, subscriptions, system settings, global integrations, system logs | Only their store: products, orders, customers, inventory, CMS, coupons, store settings |

```
Platform Dashboard → Projects → Store Dashboard
```

---

## 6. Mission Control Panel (Custom Admin)

**URL:** `/admin/` — fully custom, NOT Django's default admin. Django's default admin is moved to `/sd/`.

- Separate Django app: `apps/control/` (or `control/`).
- Built entirely with **Jinja2 + HTMX + Tailwind CSS**. Never Django Templates.
- Protected by staff/superuser login — never exposed to regular users.
- All pages extend `templates/control/base_control.jinja`.

### Structure
```
control/
  views.py
  urls.py
  services.py

templates/control/
  base_control.jinja       ← dark sidebar + topbar layout
  dashboard.jinja          ← users, revenue, activity overview
  users.jinja              ← list, search, ban, impersonate
  partials/
    _user_row.jinja        ← HTMX user table row
    _stats_card.jinja      ← HTMX live stats card
    _activity_feed.jinja   ← HTMX live activity feed
```

### Features (build in order)
1. **Dashboard** — total users, revenue, signups today, active sessions.
2. **User Manager** — list, HTMX live search, view profile, ban/unban.
3. **Activity Feed** — recent actions across the platform (HTMX polling).
4. **Stats Cards** — live KPI cards via `hx-trigger="every 30s"`.
5. **Impersonate User** — log in as any user for debugging (staff only).

### Full admin dashboard sections (per store)
Overview, Sales, Orders, Products, Categories, Inventory, Customers, Coupons, Reviews, CMS, Marketing, Payments, Shipping, Reports, Users, Settings, System.

### Design rules
- Dark sidebar, clean topbar — professional ops feel.
- Tailwind only — no Bootstrap, no external admin themes.
- HTMX for all table searches, filters, live stats.
- Alpine.js for sidebar collapse and dropdowns only.
- Mobile responsive.

### Security rules
- All control views decorated with `@staff_member_required`, or use a `ControlAccessMixin` for CBVs.
- Log all admin actions to an `AdminLog` model (see §17 Audit).
- Never allow GET requests to mutate data.
- All HTMX POST actions use `{{ csrf_input }}`.

### URLs
```python
# control/urls.py
app_name = 'control'
urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('users/', views.UserListView.as_view(), name='users'),
    path('users/<int:pk>/', views.UserDetailView.as_view(), name='user_detail'),
]

# config/urls.py
path('admin/', include('control.urls', namespace='control')),
path('sd/', admin.site.urls),
```

---

## 7. Product Management

Product types: Simple, Variable, Digital, Service.

Fields: title, SKU, slug, description, short description, images, gallery, video, brand, category, subcategory, tags, attributes, variants (variant SKU / price / sale price / cost price / stock), weight, dimensions, tax class, barcode, HSN/SAC, status, featured, new arrival, bestseller, SEO title/description/keywords, search indexing, related / cross-sell / upsell products.

---

## 8. Category Management

Nested categories with image, banner, icon, description, SEO, ordering, active/inactive, featured.

```
Fashion
 ├── Men
 │   ├── Shirts
 │   └── Jeans
 └── Women
     ├── Sarees
     └── Dresses
```

---

## 9. Inventory Management

Do not keep inventory logic in the product model alone.

Stock qty, reserved stock, available stock, low-stock threshold, adjustments, history, movement, purchase stock, sales deduction, returns, damaged stock, warehouse(s), inventory transfer, audit, alerts.

```
Product → Warehouse → Inventory → Stock Movements
```

---

## 10. Order Management

Cart → Checkout → Order → Order items.

Fields: order number, billing address, shipping address, payment status, order status, fulfillment status, shipping status, tracking number, courier, order/customer/admin notes.

Statuses: `Pending → Confirmed → Processing → Packed → Shipped → Delivered`, plus `Cancelled`, `Failed`, `Returned`, `Refunded`.

---

## 11. Payments

Providers are **pluggable** — never hardcode Razorpay into order logic.

```
PaymentProvider
 ├── Razorpay
 ├── Stripe
 ├── PayU
 └── COD
```

Features: initiation, verification, webhooks, transactions, logs, failed payments, refunds, partial refunds, reconciliation.

India first: Razorpay, COD, UPI. Then Stripe, PayU.

---

## 12. Shipping

Abstraction over zones (countries, states, cities, pincode rules), methods (flat, free, weight-based, price-based), courier integration, labels, tracking, shipment status, COD availability, delivery estimates.

Later: Shiprocket, Delhivery, Blue Dart, DTDC.

---

## 13. Customer Management

Profile, email, phone, addresses, orders, total spend, order count, last order, status, groups, tags, notes, wishlist, reviews, refund history.

Segmentation: New, Returning, VIP, Inactive, High Value.

---

## 14. Coupon & Discount Engine

Reusable module. Coupon codes; percentage / fixed / free-shipping discount; min order amount; max discount; product / category / customer-specific; first-order; usage limit; per-customer limit; start/expiry date; active/inactive.

Later: Buy X Get Y, BOGO, bundle discounts, tiered discounts.

---

## 15. CMS

Critical because frontends are separate.

- **Pages:** Home, About, Contact, Privacy, Terms, Return Policy, Shipping Policy, custom pages.
- **Content:** rich text, images, videos, buttons, sections, FAQs.
- **Banners:** hero, promotional, category, product, popup, announcement bar.

---

## 16. Storefront Configuration & Theme (API)

Expose per-store config so any frontend can consume it:

```
GET /api/v1/store/config/
```
```json
{
  "name": "My Store",
  "logo": "...",
  "currency": "INR",
  "theme": { "primary": "#000000", "secondary": "#ffffff" },
  "features": { "wishlist": true, "reviews": true, "coupons": true }
}
```

Theme config: colors, typography, logo, favicon, header, footer, buttons, product cards, homepage sections, navigation, footer menus. Same backend, different frontend per project.

**Navigation / menu builder:** main menu, footer menu, mobile menu, category menu, custom links.

---

## 17. Audit Logs

Track: who, what, when, which project, which object, old value, new value, IP.

```
Admin Rahul changed Product #123
Price: ₹999 → ₹899
Project: Store A
Time: 12:35 PM
```

---

## 18. Reviews, Wishlist, Search

- **Reviews:** star ratings, moderation, verified purchase, images, replies, report, approval.
- **Wishlist:** per-customer, add/remove, items.
- **Search:** product / SKU / category / brand / tags / attributes, price + availability filtering. MVP = PostgreSQL search. Later: Elasticsearch/OpenSearch, typo tolerance, analytics, popular searches.

---

## 19. SEO

Expose via API: meta title, meta description, canonical URL, slug, OG title/description/image, robots settings, sitemap, structured data (product schema, breadcrumb schema).

Backend rules: dynamic meta per page, clean readable URLs, semantic HTML, fast loading.

---

## 20. Notifications

Central system with **provider abstraction** (no hardcoded provider).

- **Email:** order confirmation, payment confirmation, shipment, delivery, cancellation, refund, password reset, welcome.
- **SMS / WhatsApp (later):** OTP, order confirmation, shipment, delivery.

---

## 21. Analytics / Dashboard / Reports

- **Sales:** today / weekly / monthly / total, orders, average order value.
- **Customers:** new, returning, growth.
- **Products:** best sellers, low-stock, out-of-stock.
- **Orders:** pending, processing, shipped, delivered, cancelled, returned.
- **Charts:** revenue, orders, customers, conversion, product performance.
- **Reports:** sales, order, product, customer, tax, payment, refund, inventory, coupon. Export CSV / Excel / PDF.

---

## 22. API Layer

First-class, not an afterthought.

```
/api/v1/auth/
/api/v1/store/
/api/v1/products/
/api/v1/categories/
/api/v1/cart/
/api/v1/checkout/
/api/v1/orders/
/api/v1/customers/
/api/v1/payments/
/api/v1/shipping/
/api/v1/coupons/
/api/v1/reviews/
/api/v1/wishlist/
/api/v1/cms/
/api/v1/search/
```

DRF with: API versioning, authentication, permissions, pagination, filtering, ordering, rate limiting, OpenAPI docs, webhooks.

### Webhooks
`order.created`, `order.updated`, `order.cancelled`, `payment.success`, `payment.failed`, `payment.refunded`, `shipment.created`, `shipment.delivered`, `customer.created`, `product.updated`, `inventory.low`. Sign every webhook payload; verify signatures on receipt.

---

## 23. Media Management

Product / category / banner / CMS images, user uploads. Image optimization, thumbnail generation, WebP/AVIF, file-size validation, storage abstraction. Local/volume in dev → S3 / Cloudflare R2 in prod.

---

## 24. Template Engine Config

**Jinja2 is PRIMARY:**
- Extension `.jinja`.
- Enabled before `DjangoTemplates` in settings.
- Used for all admin panel and error pages.
- **CSRF in Jinja2 = `{{ csrf_input }}`**, NOT `{% csrf_token %}`.
- Autoescape enabled (XSS prevention).

**Django Templates:** ONLY for Django's built-in admin at `/sd/`.

---

## 25. Frontend Rules (Admin Panel)

- Tailwind utility-first — no inline CSS, no custom CSS unless unavoidable.
- Always reuse components from `templates/components/`.
- Semantic HTML for SEO.
- HTMX first; Alpine.js only for UI toggles.

### HTMX rules (mandatory)
- Use HTMX instead of JavaScript wherever possible.
- Use `hx-get`, `hx-post`, `hx-target`, `hx-swap`, `hx-trigger`.
- Always return **HTML partials**, never JSON, from HTMX views.
- Partials live in `templates/partials/_*.jinja`.
- Use `hx-indicator` for loading states.
- CSRF: include `{{ csrf_input }}` in every POST form.

### Alpine.js rules (strict)
Use ONLY for: toggles, modals, dropdowns, tabs.
NEVER for: API calls, business logic, replacing HTMX.

> Note: HTMX/Alpine/Tailwind apply to the **admin panel**. Public storefronts are separate frontend projects consuming the REST API (§16, §22).

---

## 26. Django Backend Rules

- **Models:** clean, normalized, always add `__str__`. Every business model carries a `project` FK.
- **Views:** prefer CBVs. Keep views thin — logic goes in `services.py`.
- **Business logic:** always in `services.py`. Never in views or templates.
- **Forms:** use `ModelForms`. Never manual validation.
- **URLs:** app-level `urls.py`, always namespaced.

### Development workflow order
1. Models
2. Migrations (show file, wait for confirmation)
3. Forms
4. Services
5. Views
6. URLs
7. Templates (Jinja2)
8. HTMX integration

---

## 27. Database / Migration Safety Rules

- **NEVER run `migrate` automatically.**
- Always show the migration file first and wait for confirmation.
- Always run `makemigrations` before `migrate`.
- Never edit existing migrations — create new ones.

---

## 28. Security Rules

- CSRF protection always on; CORS configured for API.
- Validate all inputs via forms / DRF serializers.
- Autoescape enabled in Jinja2.
- Use Django ORM only — no raw SQL unless explicitly required.
- Never expose internal errors to the frontend.
- Object-level permissions; tenant isolation enforced server-side.
- Secure cookies; password hashing; API throttling.
- 2FA for admins; login attempt protection.
- Webhook signature verification; file upload validation.
- **Never trust `project_id` from the frontend** — resolve from domain/auth.

---

## 29. Multi-Tenancy Rules

- All business data linked to a `Project` and, where user-owned, to `request.user`.
- Always filter queries by the resolved project (and user).
- Never return data across projects or across users.
- Authentication via Django auth, extended with a `Profile` model.

---

## 30. SEO Rules (Admin-Rendered Pages)

- Dynamic `meta` title and description per page.
- Clean, readable URLs.
- Semantic HTML structure.
- Fast loading — minimal JS payload.

---

## 31. .gitignore

```
plans/
*.pyc
__pycache__/
db.sqlite3
.env
```

---

## 32. Output Format (Every Response)

When generating code, return in this order:

1. Folder structure (if new files/apps)
2. Models
3. Forms (if needed)
4. `services.py` (if needed)
5. Views
6. URLs
7. Templates (Jinja2 + HTMX)
8. Components / partials (if used)
9. `requirements.txt` (if new packages)
10. Updated `dev.txt` entry

---

## 33. Code Quality

- PEP8 compliant.
- `snake_case` naming throughout.
- DRY — no duplicated code.
- Readable and modular.
- No logic in templates.

### Anti-patterns (never do)
- Over-engineering before validating.
- Unnecessary JavaScript.
- Logic inside templates.
- Fat views (use `services.py`).
- Duplicated code.
- Running migrations without showing the file first.
- Using `{% csrf_token %}` in Jinja2 templates.
- Returning JSON from HTMX admin views.
- One giant `shop` app.
- Trusting a frontend-supplied `project_id`.

---

## 34. Recommended Architecture

```
                    ┌─────────────────────┐
                    │   Platform Admin    │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Django Backend     │
                    │  Multi-Project Core │
                    │  REST API (DRF)     │
                    │  Business Logic     │
                    │  Auth / Payments    │
                    │  Orders / Inventory │
                    │  CMS                │
                    └──────────┬──────────┘
             ┌─────────────────┼─────────────────┐
        ┌────▼────┐       ┌────▼────┐       ┌────▼────┐
        │ Store A │       │ Store B │       │ Store C │
        └────┬────┘       └────┬────┘       └────┬────┘
        Frontend A        Frontend B        Frontend C
        HTMX/Alpine       React/Next        Flutter app
        Tailwind          Tailwind          (same API)
```

---

## 35. Final Goal

Build fast → ship early → generate revenue → scale cleanly.

Every new store: **Create Project → configure domain / theme / payment / shipping → add products → connect frontend → launch.**
