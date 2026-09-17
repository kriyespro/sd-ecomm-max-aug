/* Show/hide toggle for every password field on the page, current and future
 * (works for Django-form-rendered fields and any input swapped in later by
 * htmx/Alpine — no per-form wiring needed).
 *
 * Every element this script creates is styled with inline CSS, not Tailwind
 * utility classes. This file is never scanned by Tailwind's content globs
 * (tools/build_tailwind_site.py / build_tailwind_skins.py only glob *.jinja
 * templates) — any Tailwind class named here would be silently purged from
 * every skin's compiled CSS in production, since a custom-uploaded skin's
 * own build has no way to know this script exists at all. Inline styles are
 * the only positioning that's guaranteed to survive every skin's build. */
(function () {
  var EYE = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" width="16" height="16" style="display:block"><path d="M10 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z"/><path fill-rule="evenodd" d="M.664 10.59a1.651 1.651 0 0 1 0-1.186A10.004 10.004 0 0 1 10 3c4.257 0 7.893 2.66 9.336 6.41.147.381.147.804 0 1.186A10.004 10.004 0 0 1 10 17c-4.257 0-7.893-2.66-9.336-6.41zM14 10a4 4 0 1 1-8 0 4 4 0 0 1 8 0z" clip-rule="evenodd"/></svg>';
  var EYE_OFF = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" width="16" height="16" style="display:block"><path fill-rule="evenodd" d="M3.28 2.22a.75.75 0 0 0-1.06 1.06l14.5 14.5a.75.75 0 1 0 1.06-1.06l-1.745-1.745a10.029 10.029 0 0 0 3.3-4.38 1.651 1.651 0 0 0 0-1.185A10.004 10.004 0 0 0 9.999 3a9.956 9.956 0 0 0-4.744 1.194L3.28 2.22zM7.752 6.69l1.092 1.092a2.5 2.5 0 0 1 3.374 3.373l1.091 1.092a4 4 0 0 0-5.557-5.557z" clip-rule="evenodd"/><path d="M10.748 13.93l2.523 2.523a9.987 9.987 0 0 1-3.27.547c-4.258 0-7.894-2.66-9.337-6.41a1.651 1.651 0 0 1 0-1.186A10.007 10.007 0 0 1 2.839 6.02L6.07 9.252a4 4 0 0 0 4.678 4.678z"/></svg>';

  function toggle(input, btn) {
    var showing = input.type === "text";
    input.type = showing ? "password" : "text";
    btn.setAttribute("aria-label", showing ? "Show password" : "Hide password");
    btn.innerHTML = showing ? EYE : EYE_OFF;
  }

  function wrap(input) {
    if (input.dataset.pwToggled) return;
    input.dataset.pwToggled = "1";

    var wrapper = document.createElement("div");
    wrapper.style.cssText = "position:relative;display:block;width:100%";
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);
    // !important beats any bg/border shorthand the input's own classes set
    // that would otherwise also reset padding.
    input.style.setProperty("padding-right", "2.25rem", "important");
    input.style.setProperty("box-sizing", "border-box", "important");

    var btn = document.createElement("button");
    btn.type = "button";
    btn.tabIndex = -1;
    btn.setAttribute("aria-label", "Show password");
    // Every storefront skin has its own palette (Mission Control's own
    // slate/gray theme is just one of ~18) — currentColor + opacity picks up
    // whatever text color already applies at that spot instead of a fixed
    // color that would clash with most themes.
    btn.style.cssText = [
      "position:absolute",
      "top:0", "bottom:0", "right:0.375rem",
      "display:flex", "align-items:center", "justify-content:center",
      "width:1.75rem",
      "margin:0", "padding:0", "border:0", "background:transparent",
      "color:currentColor", "opacity:0.5",
      "cursor:pointer", "transition:opacity .15s",
    ].join(";");
    btn.addEventListener("mouseenter", function () { btn.style.opacity = "0.85"; });
    btn.addEventListener("mouseleave", function () { btn.style.opacity = "0.5"; });
    btn.innerHTML = EYE;
    btn.addEventListener("click", function () { toggle(input, btn); });

    wrapper.appendChild(btn);
  }

  function scan(root) {
    (root || document).querySelectorAll('input[type="password"]:not([data-pw-toggled])').forEach(wrap);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { scan(); });
  } else {
    scan();
  }
  new MutationObserver(function () { scan(); }).observe(document.documentElement, { childList: true, subtree: true });
})();
