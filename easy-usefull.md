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
- [ ] [MODERATE] Onboarding wizard is owner/manager only, one-time. Staff
      and DGC get a checklist now (this session) but never a wizard —
      low priority, checklist already covers it reasonably.

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

- [ ] [SAFE] Trial-ending-soon reminder — Subscription.trial_end exists,
      auto-suspend-when-overdue exists (Celery), but nothing warns the
      owner *before* it happens. First one to build — smallest, safest,
      proves the pattern.
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
- [ ] [CAREFUL] 2FA for platform admins — flagged since Phase 12, still
      not built. Real value (admin accounts are the highest-privilege
      target) but needs a proper plan first: TOTP + backup codes + no
      lockout risk. Not a "just add it" task.
- [x] Webhook signature verification, payment credential handling, XSS/IDOR
      spot checks — all clean, verified this session.

## COMMERCIAL

- [ ] [SAFE] Trial-ending reminder (see RELIABLE) doubles as a conversion
      moment — same build, listed twice deliberately.
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

**Starting with**: trial-ending-soon reminder (SAFE, RELIABLE+COMMERCIAL,
smallest blast radius). Will report back before picking the next item.
