# Improvement list — from CLAUDE_GENERAL_SAAS.md, checked against actual code

Grounded in what's actually missing (checked via grep, not guessed). Each item
tagged SAFE (pure addition, zero risk to existing flows) / MODERATE (touches
existing code, needs care) / CAREFUL (money/auth-adjacent, plan before build).

Working one at a time, safest first. Test + confirm before moving to the next.

## USEFUL

- [ ] [SAFE] Abandoned-cart recovery email — classic high-ROI automation,
      explicitly named in CLAUDE_GENERAL_SAAS.md's own example. Cart rows
      already track `updated_at`; a Celery task finds carts idle N hours
      with items + an email, sends one reminder (never twice). Zero touch
      to checkout/order code — pure read + notify.
- [ ] [MODERATE] Real courier integration (Shiprocket/Delhivery) — shipping
      app already has a `Courier` ABC + registry, only `ManualCourier`
      exists. Add one real adapter behind the same interface.
- [x] Low-stock alert to the owner — done 2026-09-16 (64eb648). Turned out
      to be half-built already: Events.INVENTORY_LOW fired, template
      existed, but nothing mapped it to a send + the recipient lookup was
      dead code. Wired the map entry + a real owner-email fallback chain
      (apps.projects.services.owner_notification_email).
- [x] AI product copy — built, currently on hold (UI hidden). Re-enable
      once ready; no work needed, just a flag flip.

## EASY

- [ ] [SAFE] Extend guided-tour coverage past the 7 shipped pages (Coupons,
      Domains, Team, Theme customization) — same engine, just more
      `data-tour` attributes + registry entries.
- [ ] [MODERATE] Onboarding wizard is owner/manager only, one-time. Staff
      and DGC get a checklist now (this session) but never a wizard —
      low priority, checklist already covers it reasonably.

## FAST

- [ ] [MODERATE] Re-check PDP query count — a years-old dev.txt note says
      "~24 queries", but Celery + caching landed since. Needs fresh
      measurement (django-prometheus is wired), not a guess.
- [x] Storefront perf cache (Host→store + chrome), edge cache, asset perf —
      already done.

## RELIABLE

- [ ] [SAFE] Trial-ending-soon reminder — Subscription.trial_end exists,
      auto-suspend-when-overdue exists (Celery), but nothing warns the
      owner *before* it happens. First one to build — smallest, safest,
      proves the pattern.
- [ ] [MODERATE] Payment-webhook failure path note from early dev.txt: a
      failed verify_payment leaves Payment PENDING with no payment.failed
      event (rolled back inside the same atomic block). Worth re-verifying
      still true before touching — payments code is the highest-risk
      surface in the app.

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
- [ ] [MODERATE] "Upgrade" nudge when a store hits a plan limit (seat cap,
      etc.) — billing_limits.usage() already exists and is used to gray
      out the "add team member" button; extend the same signal into a
      visible upsell instead of just a disabled state.

## MAINTAINABLE

- [x] No outstanding complaints from this session's code-review passes.

## SCALABLE

- [x] Media on STORAGES backend (S3/R2-ready), Redis caching, Celery
      background work — already in place from earlier phases.

---

**Starting with**: trial-ending-soon reminder (SAFE, RELIABLE+COMMERCIAL,
smallest blast radius). Will report back before picking the next item.
