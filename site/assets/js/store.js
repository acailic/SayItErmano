/* SayItErmano store wiring (#17) — Paddle.js overlay checkout.
 *
 * Progressive enhancement, like the rest of this site: with the store
 * ids unset (store-config.js), with JS disabled, or when paddle.com
 * fails to load, the buy CTA remains a working plain link and no
 * third-party request happens. When armed, a click opens the Paddle
 * overlay checkout — Paddle is the merchant of record (payment, VAT,
 * invoicing, refunds).
 *
 * API per developer.paddle.com (Paddle.js v2, verified 2026-11-21):
 * load https://cdn.paddle.com/paddle/v2/paddle.js, call
 * Paddle.Environment.set("sandbox") before Paddle.Initialize({token}),
 * then Paddle.Checkout.open({items, settings, customData}).
 */
(function () {
  "use strict";

  var cfg = window.SAYIT_STORE || {};
  var cta = document.getElementById("buy-cta");
  if (!cta) return;

  var armed = Boolean(
    cfg.clientToken && cfg.priceId &&
    (cfg.mode === "sandbox" || cfg.mode === "live")
  );
  if (!armed) return; // unprovisioned: plain link, nothing loaded

  /* Funnel tags (#18): the landing-CTA utm ids travel with the
     transaction as Paddle custom_data — Paddle's documented UTM
     pass-through for checkout attribution (server-side measurement
     only; the page itself adds no client telemetry). */
  function utmData() {
    return {
      utm_source: cta.getAttribute("data-utm-source") || "landing",
      utm_medium: cta.getAttribute("data-utm-medium") || "cta",
      utm_campaign: cta.getAttribute("data-utm-campaign") || "store",
    };
  }

  var script = document.createElement("script");
  script.src = "https://cdn.paddle.com/paddle/v2/paddle.js";
  script.async = true;

  script.onload = function () {
    var Paddle = window.Paddle;
    if (!Paddle || !Paddle.Initialize) return; // stay a plain link

    if (cfg.mode === "sandbox") Paddle.Environment.set("sandbox");

    Paddle.Initialize({
      token: cfg.clientToken,
      eventCallback: function (ev) {
        if (ev && ev.name === "checkout.completed") {
          var next = document.getElementById("purchase-next");
          if (next) next.hidden = false;
          if (cta.focus) cta.focus({ preventScroll: true });
        }
      },
    });

    cta.addEventListener("click", function (e) {
      e.preventDefault();
      var dark = window.matchMedia &&
        window.matchMedia("(prefers-color-scheme: dark)").matches;
      Paddle.Checkout.open({
        items: [{ priceId: cfg.priceId, quantity: 1 }],
        settings: {
          displayMode: "overlay",
          variant: "one-page",
          theme: dark ? "dark" : "light",
          locale: "en",
        },
        customData: utmData(),
      });
    });
  };

  document.head.appendChild(script);
})();
