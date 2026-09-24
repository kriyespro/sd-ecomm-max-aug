/* First-party analytics beacon -- drives the Mission Control dashboard's
 * "Visitors today" / funnel / live-visitors widgets (see
 * apps.analytics.services, apps.shopfront.views.BeaconView).
 *
 * Storefront pages are CDN-edge-cacheable (apps.shopfront.middleware), so a
 * real visitor's page load often never reaches Django at all -- this script
 * is the only thing that reliably sees every real page view regardless of
 * where the HTML came from. It has no third-party dependency and sends no
 * data anywhere but this store's own origin. Fails silent everywhere
 * (ad blockers, old browsers, sendBeacon absent) -- never breaks the page.
 */
(function () {
  "use strict";

  function isProductPage() {
    return /\/p\/[^/]+\/?$/.test(location.pathname);
  }

  function refCode() {
    try {
      return new URLSearchParams(location.search).get("ref") || "";
    } catch (e) {
      return "";
    }
  }

  function send(kind) {
    var payload = {
      kind: kind,
      event: isProductPage() ? "product_view" : "page_view",
      path: location.pathname,
      referrer: document.referrer || "",
    };
    if (kind !== "heartbeat") {
      var ref = refCode();
      if (ref) payload.ref = ref;
    }
    var body = Object.keys(payload)
      .map(function (k) { return k + "=" + encodeURIComponent(payload[k]); })
      .join("&");
    var url = "/beacon/";
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon(
          url,
          new Blob([body], { type: "application/x-www-form-urlencoded" })
        );
        return;
      }
    } catch (e) { /* fall through to fetch */ }
    if (window.fetch) {
      fetch(url, {
        method: "POST",
        body: body,
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        keepalive: true,
        credentials: "same-origin",
      }).catch(function () {});
    }
  }

  send("view");
  setInterval(function () { send("heartbeat"); }, 25000);
})();
