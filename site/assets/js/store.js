/* SayItErmano store — Paddle.js overlay checkout (issue #17).
 *
 * The config block below IS the sandbox/live flag (the site has no
 * build step — this file ships verbatim):
 *
 *   1. sandbox (ship state): keep environment "sandbox" and paste the
 *      sandbox client-side token + price id.
 *   2. go-live flip (one reviewed edit): environment "live" plus the
 *      live token / live price id, then push.
 *
 * While either id contains "REPLACE", buy buttons stay passive and
 * their href fallback (the reserve discussion) keeps working.
 *
 * Paddle.js loads lazily on the FIRST buy click: the landing page
 * makes no third-party request unless a visitor tries to buy, which
 * keeps the site's "no tracking, no cookies" claim true for everyone
 * else. UTM tags ride along as checkout customData — Paddle's
 * documented attribution pass-through (docs/pricing-funnel-decision-rule.md).
 */
window.SAYIT_STORE = {
  environment: "sandbox",
  clientToken: "test_REPLACE_WITH_SANDBOX_CLIENT_SIDE_TOKEN",
  priceId: "pri_REPLACE_WITH_SANDBOX_PRICE_ID"
};

(function () {
  "use strict";

  var PADDLE_CDN = "https://cdn.paddle.com/paddle/v2/paddle.js";
  var UTM_ATTRS = ["utm_source", "utm_medium", "utm_campaign"];

  var cfg = window.SAYIT_STORE || {};
  var initialized = false;
  var loading = null;
  var opening = false;

  function configured() {
    return Boolean(cfg.clientToken && cfg.priceId
      && cfg.clientToken.indexOf("REPLACE") === -1
      && cfg.priceId.indexOf("REPLACE") === -1);
  }

  function utmData(el) {
    var out = {};
    UTM_ATTRS.forEach(function (name) {
      var v = el.getAttribute("data-" + name.replace(/_/g, "-"));
      if (v) out[name] = v;
    });
    return out;
  }

  function loadPaddle() {
    if (window.Paddle) return Promise.resolve();
    if (loading) return loading;
    loading = new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = PADDLE_CDN;
      s.async = true;
      s.onload = function () { resolve(); };
      s.onerror = function () {
        loading = null;
        reject(new Error("paddle.js failed to load"));
      };
      document.head.appendChild(s);
    });
    return loading;
  }

  function openCheckout(el) {
    if (!initialized) {
      Paddle.Environment.set(cfg.environment === "live" ? "live" : "sandbox");
      Paddle.Initialize({ token: cfg.clientToken });
      initialized = true;
    }
    var dark = window.matchMedia
      && window.matchMedia("(prefers-color-scheme: dark)").matches;
    Paddle.Checkout.open({
      items: [{ priceId: cfg.priceId, quantity: 1 }],
      customData: utmData(el),
      settings: { displayMode: "overlay", theme: dark ? "dark" : "light" }
    });
  }

  Array.prototype.forEach.call(
    document.querySelectorAll("[data-paddle-checkout]"),
    function (el) {
      el.addEventListener("click", function (ev) {
        if (!configured()) return; /* placeholder ids: href fallback (reserve link) */
        ev.preventDefault();
        if (opening) return;
        opening = true;
        el.setAttribute("aria-busy", "true");
        loadPaddle()
          .then(function () { openCheckout(el); })
          .catch(function () {
            /* CDN unreachable — send the buyer through the fallback link */
            window.location.href = el.href;
          })
          .then(function () {
            opening = false;
            el.removeAttribute("aria-busy");
          });
      });
    }
  );

  if (!configured() && window.console && console.info) {
    console.info("[store] Paddle ids not configured yet — buy CTA falls back to the reserve link");
  }
})();
