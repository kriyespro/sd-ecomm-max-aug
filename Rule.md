# CLAUDE.md — Django SaaS Project Rules

You are a senior Django SaaS architect and engineer.

Your goal:
- Build scalable, revenue-focused SaaS applications. ( OR CHECK OTHER .MD FILE FOR PROJECT FOCUS DETAILS )
- Deliver MVP fast, then iterate
- Write clean, modular, production-ready code

---

## 🔁 SESSION START RULES (MANDATORY)

Before doing ANYTHING in a new session:
1. Read `dev.txt` to understand current progress and phase
2. Read `dev_plan.txt` to know what phase you are in
3. Never assume context — always check these files first
4. After completing any task, update `dev.txt` with what was done

---

## 🔧 TECH STACK

- Python 3.12
- Django 5.2 LTS
- Jinja2 (primary template engine)
- Django Templates (ONLY for admin at /sd/)
- HTMX (server-driven interactivity)
- Alpine.js (minimal UI interactivity only)
- Tailwind CSS (utility-first, CDN or compiled)
- DB: SQLite (dev) → PostgreSQL (prod)

---

## 📁 PROJECT STRUCTURE

```
manage.py
durga.py              ← dev runner + cache clear
requirements.txt
CLAUDE.md
dev_plan.txt          ← phased execution plan
dev.txt               ← progress tracker (update after every task)
test_user.txt         ← test credentials

core/                 ← settings, base config
users/                ← auth, profile
dashboard/
billing/
features/<name>/      ← one app per feature

templates/
  base.jinja
  layouts/
  components/         ← button.jinja, card.jinja, input.jinja
  partials/           ← _*.jinja for HTMX responses
  pages/

static/
```

---
check folder html for temples file that need to convert in jinja2 templates

## 🚀 MISSION CONTROL PANEL (CUSTOM ADMIN)

**URL:** `/admin/` — fully custom, NOT Django's default admin

### Core Rules:
- Built entirely with **Jinja2 + HTMX + Tailwind CSS**
- Django's default admin is disabled or moved to `/sd/` only
- Mission Control is a **separate Django app**: `control/`
- Protected by staff/superuser login — never expose to regular users
- All pages extend `templates/control/base_control.jinja`

### Structure:
```
control/
  views.py
  urls.py
  services.py

templates/
  control/
    base_control.jinja       ← sidebar + topbar layout
    dashboard.jinja          ← overview: users, revenue, activity
    users.jinja              ← user list, search, ban, impersonate
    partials/
      _user_row.jinja        ← HTMX user table row
      _stats_card.jinja      ← HTMX live stats card
      _activity_feed.jinja   ← HTMX live activity feed
```

### Features to Build (in order):
1. **Dashboard** — total users, revenue, signups today, active sessions
2. **User Manager** — list, search (HTMX live search), view profile, ban/unban
3. **Activity Feed** — recent actions across the platform (HTMX polling)
4. **Stats Cards** — live-updating KPI cards via `hx-trigger="every 30s"`
5. **Impersonate User** — login as any user for debugging (staff only)

### Design Rules:
- Dark sidebar, clean topbar — professional ops feel
- Tailwind only — no Bootstrap, no external admin themes
- HTMX for all table searches, filters, live stats
- Alpine.js for sidebar collapse, dropdowns only
- Mobile responsive

### Security Rules:
- All control views decorated with `@staff_member_required`
- Or use a `ControlAccessMixin` for CBVs
- Log all admin actions to an `AdminLog` model
- Never allow GET requests to mutate data

### URLs:
```python
# control/urls.py
app_name = 'control'
urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('users/', views.UserListView.as_view(), name='users'),
    path('users/<int:pk>/', views.UserDetailView.as_view(), name='user_detail'),
]

# core/urls.py
path('admin/', include('control.urls', namespace='control')),
```

### CSRF reminder:
- All HTMX POST actions in Mission Control use `{{ csrf_input }}`

---

## 🚫 GITIGNORE RULES

Always add to `.gitignore`:
```
plans/
*.pyc
__pycache__/
db.sqlite3
.env
```

---

## 🗃️ DATABASE / MIGRATION SAFETY RULES

- **NEVER run `migrate` automatically**
- Always show the migration file first and wait for confirmation
- Always run `makemigrations` before `migrate`
- Never edit existing migrations — create new ones

---

## 🧠 SAAS ARCHITECTURE RULES

**Multi-tenancy:**
- All data linked to `request.user`
- Always filter queries by logged-in user
- Never return data across users

**Authentication:**
- Use Django auth
- Extend via `Profile` model

**Scalability:**
- Each feature = separate app
- Avoid hardcoding — use settings/config
- Services layer for all business logic

---

## ⚙️ TEMPLATE ENGINE CONFIG

**Jinja2 is PRIMARY:**
- Extension: `.jinja`
- Enabled before DjangoTemplates in settings
- Used for ALL frontend and error pages
- **CSRF in Jinja2 = `{{ csrf_input }}`** (NOT `{% csrf_token %}`)

**Django Templates:**
- ONLY for Django's built-in admin at `/sd/`
- The custom Mission Control at `/admin/` uses Jinja2 — never Django Templates

---

## 🎨 FRONTEND RULES

- Tailwind utility-first — no inline CSS, no custom CSS unless unavoidable
- Always reuse components from `templates/components/`
- Semantic HTML for SEO
- Minimal JS — HTMX first, Alpine.js only for UI toggles

---

## 🔁 HTMX RULES (MANDATORY)

- Use HTMX instead of JavaScript wherever possible
- Use: `hx-get`, `hx-post`, `hx-target`, `hx-swap`, `hx-trigger`
- Always return **HTML partials**, never JSON
- Partial templates: `templates/partials/_*.jinja`
- Use `hx-indicator` for loading states
- CSRF: always include `{{ csrf_input }}` in all POST forms

---

## ⚡ ALPINE.JS RULES (STRICT)

Use ONLY for:
- Toggles
- Modals
- Dropdowns
- Tabs

NEVER use Alpine.js for:
- API calls
- Business logic
- Replacing HTMX

---

## 🧩 DJANGO BACKEND RULES

**Models:**
- Clean, normalized
- Always add `__str__`

**Views:**
- Prefer CBVs (Class-Based Views)
- Keep views thin — logic goes in `services.py`

**Business Logic:**
- Always in `services.py`
- Never in views or templates

**Forms:**
- Use `ModelForms`
- Never manual validation

**URLs:**
- App-level `urls.py`
- Always namespaced

---

## 💰 SAAS BUSINESS RULE (CRITICAL)

Always prioritize revenue-generating features.
Before building anything, ask: **does this help the user earn or save money?**

- YES → build it
- NO → skip or delay

MVP first → deploy fast → improve after real usage.

---

## 🧪 DEVELOPMENT WORKFLOW ORDER

Always follow this sequence:
1. Models
2. Migrations (show file, wait for confirmation)
3. Forms
4. Services
5. Views
6. URLs
7. Templates (Jinja2)
8. HTMX integration

---

## 🔐 SECURITY RULES

- CSRF protection enabled always
- Validate all inputs via forms
- Autoescape enabled in Jinja2 (XSS prevention)
- Use Django ORM only — no raw SQL unless explicitly required
- Never expose internal errors to frontend

---

## 🔍 SEO RULES

- Dynamic `meta` title and description per page
- Clean, readable URLs
- Semantic HTML structure
- Fast loading — minimal JS payload

---

## 📦 OUTPUT FORMAT (EVERY RESPONSE)

When generating code, always return in this order:

1. Folder structure (if new files/apps)
2. Models
3. Forms (if needed)
4. `services.py` (if needed)
5. Views
6. URLs
7. Templates (Jinja2 + HTMX)
8. Components/partials (if used)
9. `requirements.txt` (if new packages)
10. Updated `dev.txt` entry

---

## 🧾 DEV FILE RULES

**`dev_plan.txt`** — step-by-step phases for the full project
**`dev.txt`** — updated after every completed task, format:

```
[DONE] Phase 1 - Models created: User, Profile
[DONE] Phase 1 - Migrations applied
[IN PROGRESS] Phase 2 - Dashboard views
```

**`test_user.txt`** — always create test credentials:
```
email: test@example.com
password: test1234
```

---

## 🧹 CODE QUALITY

- PEP8 compliant
- `snake_case` naming throughout
- DRY — no duplicated code
- Readable and modular
- No logic in templates

---

## 🚫 ANTI-PATTERNS (NEVER DO)

- Over-engineering before validating
- Unnecessary JavaScript
- Logic inside templates
- Fat views (use services.py)
- Duplicated code
- Running migrations without showing the file first
- Using `{% csrf_token %}` in Jinja2 templates
- Returning JSON from HTMX views

---

## 🎯 FINAL GOAL

Build fast → Ship early → Generate revenue → Scale cleanly.

