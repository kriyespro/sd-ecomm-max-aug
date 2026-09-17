# Improvement list — from CLAUDE_GENERAL_SAAS.md, checked against actual code

Grounded in what's actually missing (checked via grep, not guessed). Each item
tagged SAFE (pure addition, zero risk to existing flows) / MODERATE (touches
existing code, needs care) / CAREFUL (money/auth-adjacent, plan before build).

Working one at a time, safest first. Test + confirm before moving to the next.

## USEFUL

- [x] Abandoned-cart recovery email — done 2026-09-16 (a7e17d9). Hourly
      Celery task, one email max per cart. Scope limit: only reaches a
      registered shopper's account email or a Cart.email some flow set —
      a guest cart with no captured email can't be reached yet (storefront
      doesn't ask for one before checkout starts). Follow-up if wanted:
      capture email earlier in the cart/checkout flow — UI-touching,
      bigger scope, not done now.
- [x] Real courier integration — done 2026-09-16 (48bed0d). Shiprocket +
      Delhivery adapters (urllib, no SDK, same convention as Razorpay),
      test mode default (synthetic tracking, no network call) so it's safe
      with zero keys — site owner adds real ones at
      /admin/shipping/couriers/ whenever ready. Fixed two real bugs found
      while wiring it: create_shipment() built the courier with no config
      (self.config always {}), and the live API call had no error
      handling inside the atomic block — a courier failure would have
      rolled back the Shipment it just created. Both fixed + regression
      tested. 18 new tests, full suite (996) green.
- [x] Low-stock alert to the owner — done 2026-09-16 (64eb648). Turned out
      to be half-built already: Events.INVENTORY_LOW fired, template
      existed, but nothing mapped it to a send + the recipient lookup was
      dead code. Wired the map entry + a real owner-email fallback chain
      (apps.projects.services.owner_notification_email).
- [x] AI product copy — built, currently on hold (UI hidden). Re-enable
      once ready; no work needed, just a flag flip.

## EASY

- [x] Extend guided-tour coverage — done 2026-09-16 (2770c9b). Coupons,
      Domains, Team, Theme — 11 tours total now.
- [x] Onboarding wizard extension — done 2026-09-16 (b4d3dec). Staff
      genuinely has nothing to configure, left as-is. DGC's checklist now
      tracks real progress (store managed, payout UPI, affiliate link) —
      was hardcoded to always-incomplete. Found + fixed a real dead end
      along the way: the checklist's third step linked to a superuser-only
      page, 403ing any plain DGC who followed their own checklist.

## FAST

- [x] Re-checked PDP query count 2026-09-16 — measured with
      CaptureQueriesContext: 31 queries, but every one is a single fixed
      lookup (chrome bits: theme/skin/banners/menu/budget-bands/benefits/
      Instagram/shorts/SEO/reviews/related/shipping/inventory) — no N+1
      loop anywhere. Reasonable for how many independent CMS content types
      the page assembles; not a real problem right now. Per
      CLAUDE_GENERAL_SAAS.md's own PERFORMANCE rule ("measure before
      introducing complicated optimization infrastructure") — measured,
      no action taken. Revisit only if real traffic data says otherwise.
- [x] Storefront perf cache (Host→store + chrome), edge cache, asset perf —
      already done.

## RELIABLE

- [x] Trial-ending-soon reminder — done 2026-09-16 (41e0721). Twice-daily
      Celery task, owner email once 3 days out (matches the existing
      Mission Control banner's own threshold), reuses the low-stock
      alert's owner_notification_email fallback chain. Skips DGC-managed
      stores on purpose — their team never sees the billing screen.
- [x] Fixed 2026-09-16 — the old note was still accurate. verify_payment's
      `@transaction.atomic` rolled back the failure-path save (Payment.FAILED
      + PaymentEvent + the payment.failed domain event) on the very `raise`
      that reported the failure — a bad signature looked identical, in the
      database, to a verification that was never attempted. Fixed by
      dropping the outer atomic (success path stays atomic via _settle()'s
      own decorator). Confirmed the regression test fails on the old code,
      passes on the fix. This was apps.payments' *first* test file — a real
      gap the RELIABLE/TESTING sections both called out. Full suite (972
      tests) green before pushing, given the blast radius.

## SECURE

- [x] Open redirect — found + fixed project-wide this session (2026-09-16).
- [x] 2FA for platform admins — done 2026-09-16 (b564bfb). Confirmed scope
      first: TOTP + backup codes (not TOTP alone), mandatory from day one
      (not opt-in), recovery = backup codes or another platform admin
      resetting it from /admin/users/. Gate is scoped to /admin/ only
      (Mission Control) — a superuser's incidental storefront/API request
      isn't blocked, only actual platform-admin tooling. Existing sessions
      aren't force-logged-out, just routed to setup on next /admin/ hit —
      avoids a self-inflicted lockout of every admin at once. 20 dedicated
      tests (service, setup flow, login handshake, middleware gate, admin
      reset), full suite (1016) green.
- [x] Webhook signature verification, payment credential handling, XSS/IDOR
      spot checks — all clean, verified this session.
- [x] 2FA follow-ups — done 2026-09-17. Broadened from platform-admin-only
      to every Mission Control account (is_staff — owner/manager/staff/
      DGC too), added a real scannable QR code to setup (was manual-key
      only), and no 2FA wall on signup — a brand-new Google signup gets
      one grace session, then it's mandatory from the very next login.

## COMMERCIAL

- [x] Trial-ending reminder (see RELIABLE) — same build, doubles as a
      conversion moment.
- [x] Upgrade nudge on plan limits — done 2026-09-16. Products and Domains
      screens had NO usage display at all before (only Team did, and only
      as passive text). All three now show "X/Y used" and, at cap, a
      clickable "Upgrade plan" link to Plan & billing — same
      billing_limits.usage() already used for server-side enforcement.
      Follow-up closed same session: check_can_add_domain() existed but
      was never called anywhere — wired it into DomainAddView (mirrors
      the existing product/staff pattern exactly), so the domain cap is
      now actually enforced, not just displayed.

## MAINTAINABLE

- [x] No outstanding complaints from this session's code-review passes.

## SCALABLE

- [x] Media on STORAGES backend (S3/R2-ready), Redis caching, Celery
      background work — already in place from earlier phases.

---

## LESS FRICTION — round 1 (CLAUDE_GENERAL_SAAS.md sec 4/44)

Audited 7 workflows (product create, order fulfillment, bulk actions,
coupons, team invite, domains, dashboard). All 4 real gaps found were
built same session — see git log 6a59edb..bcb6330:

- [x] Coupon code Generate button.
- [x] Bulk activate/deactivate/archive on the product list.
- [x] Order fulfillment: set-shipping-method + create-shipment merged
      into one submit for the common first-shipment case.
- [x] Product create form accepts images in the same Save (was a hard
      2-screen "save first, then upload" requirement).

Team invite, domain verify, Today dashboard: already good, no change.

## LESS FRICTION — round 2

Audited 10 more areas not covered by round 1. All 6 real gaps found were
built same session — see git log ab796d5..3ee2a9a:

- [x] Bulk archive/unarchive on the order list.
- [x] CSV export on the customer list (orders already had one).
- [x] Bulk assign customers to a group.
- [x] Bulk approve/reject on review moderation — loops moderate_review()
      per row (not a raw bulk update) since each review touches its own
      product's rating aggregate, and a batch can span several products.
- [x] Bulk trash on the media library — upload already took multiple
      files in one submit, delete was one-at-a-time until now.
- [x] Bulk restock on Inventory — adds a shared quantity to every
      selected item in one submit (one shipment batch landing across
      several SKUs), loops inv.receive_stock() per item since each call
      locks the row and writes a real StockMovement ledger entry.

Category/Brand/Tag CRUD, Reports/export, Store profile/theme, Notification
setup, duplicate data entry: already good, no change (see round-2 audit
notes in git history for why).

Audited and already good, no gap found: Category/Brand/Tag CRUD (low
cardinality, ~5-20 rows per store — bulk actions here would be
over-engineering per CLAUDE_GENERAL_SAAS.md sec 15's own warning),
Reports/CSV export (already one screen, filters carry into the export
link, uncapped), Store profile/theme setup (checked for
type-the-same-thing-twice against onboarding — it's the same
`StoreProfile` row, onboarding just pre-fills it, not re-asked blank),
Notification/webhook setup (every built-in event already works with zero
config via `apps.notifications.defaults.DEFAULTS`, confirmed by this
session's own low-stock/trial-reminder work), duplicate data entry
generally (no manual "create order" screen exists — orders only
originate from storefront checkout, so nothing re-asks an already-on-file
customer's details).

**Not started** — plan only per explicit request. Suggested build order
if greenlit: the four SAFE ones first (same proven pattern, low risk,
quick), media bulk-delete and inventory bulk-restock after (need a bit
more design care each).

## ROUND 3 — remaining CLAUDE_GENERAL_SAAS.md pillars

Audited sections not yet specifically checked this session: 13 (UX
states), 14 (forms), 22 (error handling), 32 (accessibility), 33
(responsive), 38 (docs currency). All 3 real gaps built same session —
see git log ad5634c..27cb434:

- [x] Courier booking failure is invisible to the merchant.
      `create_shipment()` (`apps/shipping/services.py:158-165`) already
      catches `CourierError` and degrades gracefully — correct — but the
      reason it stores in `shipment.notes` is never displayed anywhere
      (`templates/control/orders/order_detail.jinja` — zero hits), and
      `OrderSetShippingAndShipView` (`apps/control/shipping_views.py:201`)
      shows the same green "Shipping set and shipment created" message
      whether the courier booking actually succeeded or silently failed.
      A bad Shiprocket/Delhivery credential looks identical, to the
      merchant, to a working one. Fix: surface `shipment.notes` on the
      order detail page when present; have the view check for it and
      show a warning-tier message instead of success.
- [x] Bulk-select checkboxes have no accessible name. Every
      checkbox added this session (product/order/customer/review/media/
      inventory — 6 templates) is a bare `<input>` outside the labeled
      `_forms.jinja` macro (which itself is confirmed clean — every real
      form field is properly paired with `<label for="">`). A screen
      reader announces only "checkbox, not checked." Fix:
      `aria-label="Select {{ row }}"` per row, `aria-label="Select all"`
      on each header checkbox — same six templates.
- [x] DEPLOY.md is stale. Grepped for pyotp/qrcode/2FA/idle/
      courier/Shiprocket/Delhivery — zero hits on all of them, despite
      DEPLOY.md already maintaining a "what's in the box" table and a
      Celery task table listing the other scheduled jobs. A fresh deploy
      following it today wouldn't know 2FA is now mandatory on first
      `/admin/` visit, that idle sessions log out after 35 min, or that
      two new pip dependencies exist. Pure documentation addition.
- [ ] [SAFE, optional/minor] Coupons and Team list empty states are bare
      ("No coupons.", "No team members yet.") vs. the "no X yet — add
      one" pattern with an inline link already used elsewhere in this
      same codebase (e.g. the product list). Lowest priority on this
      list — cosmetic, not a real workflow blocker.

Domains and Webhooks empty states already checked out fine, no change
needed. Responsive/mobile not re-checked in depth — nothing built this
session introduced a new non-table/non-form UI shape that would regress
the existing mobile-responsive pass.

## ROUND 4 — home page CMS editability + auto-scroll (user-requested)

Built same session — see git log ff3c056..c19d71d:

- [x] Trust strip (Free shipping / COD / 7-day returns / Clean formulas —
      the 4-badge row right under the hero) was hardcoded in botanica2 +
      botanica3 home.jinja — a code comment literally said "EDIT HOMEPAGE
      COPY HERE." Now editable at Mission Control → Storefront → Home
      page benefits, via a second `BenefitItem.kind` (`why` / `trust`) on
      the existing "Why it works" model + screen rather than a new one —
      same icon+title+description shape, same fallback-when-empty
      convention. Found + fixed a real bug while wiring it: store
      backup/restore's field allowlist for this model didn't include the
      new `kind` field, which would have silently dropped it.
- [x] Shorts (shared partial, effectively all 18 skins) and the
      Instagram feed (botanica3 only — no other skin has this section)
      now auto-scroll via `auto_scroll_attrs()`, same macro as the
      category-row overflow case. Instagram's static 6-tile grid became
      a scroll row, slice raised to 12.

## ROUND 5 — sections 6/20/21/24/30 (planning only, not built yet)

Fresh angles not covered by rounds 1-4: one-primary-action screens,
background-work risk in this session's *own* new code, revenue-rule
expansion/referral nudges, jargon leaks on the 6 new bulk screens,
data-isolation spot check on those same 6. Grounded via code read —
file:line in each note.

- [ ] [MODERATE] Bulk-action requests have no row cap and no timeout
      margin. `gunicorn.conf.py:21` sets `timeout = 30`. None of the 6
      bulk views built this session
      (`apps/control/catalog_views.py:296`, `order_views.py:118`,
      `customer_views.py:111`, `review_views.py:46`,
      `phase11_views.py:340`, `inventory_views.py:156`) cap
      `request.POST.getlist("pks")`. Product/order/customer bulk actions
      are a single `.update()` each — fast even at scale, low risk. The
      other 3 genuinely loop per-row inside the request with real
      per-item work: review bulk-approve calls `moderate_review()` (save
      + `refresh_product_rating()`, a second query+save, per review),
      media bulk-trash loops `trash_asset()`, inventory bulk-receive
      loops `inv.receive_stock()` (row lock + `StockMovement` write per
      item). A merchant selecting thousands of rows on any of these 3
      risks a mid-batch 30s timeout with no wrapping transaction, so a
      partial batch would apply with no rollback. Fix: cap `pks` length
      (e.g. reject/truncate over ~500 with a clear message), and/or move
      the 3 looping ones to a Celery task above some threshold.
- [ ] [SAFE] No "approaching your limit" nudge on plan usage.
      `catalog_views.py:273` / `domain_views.py:48` only check
      `used >= cap` — binary, at-the-wall. Nothing warns a merchant
      trending toward the cap (e.g. `used >= cap * 0.8`) before they
      actually hit the block.
- [ ] [SAFE/MODERATE] Referral program is invisible to store owners.
      `my_commissions` / `affiliate_overview` are `_DGC_ONLY` /
      `_PLATFORM_ADMIN_ONLY` in `navigation.py` — grepped
      `dashboard.jinja` / `store_dashboard.jinja` for any affiliate/
      referral mention, zero hits. A plain store owner is never invited
      to refer another merchant from inside their own Mission Control at
      all, despite CLAUDE_GENERAL_SAAS.md sec 20's explicit "why would
      they recommend the product?" question. Needs a scope decision
      (where would an owner's own referral link even live?) before
      building — not a pure SAFE addition like the others.

Clean, no gap: one-primary-action screens (order detail, product form —
each card/screen already has one clear dark "primary" button vs bordered
secondaries, checked), jargon leaks on the 6 bulk screens (grepped for
raw exception names / `is_active`/`None`/`null` as visible text — zero
hits, matches the views' own plain-English messages), data isolation on
all 6 bulk views (every one filters by `project=self.active_project`
before mutating — confirmed pattern-consistent, no view skips it).
