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

## LESS FRICTION — round 2 (planning only, not built yet)

Audited 10 more areas not covered by round 1. Each recommendation below
is the same proven shape as round 1's bulk-product fix (checkbox +
toolbar + one audit-log row for the batch) unless noted. Grounded via
code read, not guessed — file:line in each note.

- [ ] [SAFE] Bulk archive on the order list. `OrderListView`
      (`apps/control/order_views.py:55`) already has search + status +
      payment filters + CSV export; row action is Archive/Unarchive
      one-at-a-time. A merchant clearing a month of delivered orders does
      it one row at a time today. Same checkbox+toolbar shape as the
      product list.
- [ ] [SAFE] CSV export on the customer list. `CustomerListView`
      (`apps/control/customer_views.py:25`) has search + segment filter
      but no export — orders already have one, customers don't. Common
      ask for marketing/CRM tools. Pure addition, mirrors the existing
      order export exactly.
- [ ] [SAFE] Bulk assign customers to a group. Same screen — moving many
      existing customers into a new `CustomerGroup` is one-at-a-time via
      `CustomerUpdateView` today.
- [ ] [SAFE] Bulk approve/reject on review moderation.
      `ReviewModerateView` (`apps/control/review_views.py:36`) approves
      one review per POST; the list already shows a pending count and
      status filter, so a merchant with 10 pending reviews clicks 10
      times. Same shape as the product bulk-status fix.
- [ ] [MODERATE] Bulk delete on the media library.
      `MediaDeleteView`/`MediaRestoreView`/`MediaPurgeView`
      (`apps/control/phase11_views.py:240,277,321`) are strictly
      one-asset-per-request, no checkbox UI in `library.jinja` at all —
      asymmetric, since upload already accepts multiple files in one
      submit. Tagged MODERATE not SAFE because delete is destructive
      (media may be in use on live product pages) — needs a confirm step
      and a check for in-use assets before batching, not just a raw bulk
      `.update()`/delete.
- [ ] [MODERATE] Bulk restock (quantity add, not a status toggle) on
      Inventory. Already has search + warehouse + low-stock filter and
      an inline HTMX adjust row (no page nav) — already fairly good.
      Lower priority: unlike a status flip, "add N units" is a per-row
      value, not one action applied to every selected row, so the UI
      needs a per-row quantity input inside the bulk form, not just a
      dropdown — more design work than the others on this list.

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
