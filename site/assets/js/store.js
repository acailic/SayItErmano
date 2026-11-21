/* SayItErmano store wiring (#17) — Paddle.js overlay checkout.
 *
 * Progressive enhancement, like the rest of this site: with the store
 * ids unset (store-config.js), with JS disabled, or when paddle.com
 * fails to load, the buy CTAs remain working plain links and no
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
  /* Every store CTA on the page: the pricing-card button (#buy-cta,
     #17) plus any closing link marked data-store-buy (#19). All carry
     their own utm data tags, so customData is read per element. */
  var ctas = Array.prototype.slice.call(
    document.querySelectorAll("#buy-cta, [data-store-buy]")
  );
  if (!ctas.length) return;

  var armed = Boolean(
    cfg.clientToken && cfg.priceId &&
    (cfg.mode === "sandbox" || cfg.mode === "live")
  );
  if (!armed) return; // unprovisioned: plain links, nothing loaded

  /* Funnel tags (#18): the landing-CTA utm ids travel with the
     transaction as Paddle custom_data — Paddle's documented UTM
     pass-through for checkout attribution (server-side measurement
     only; the page itself adds no client telemetry). */
  function utmData(el) {
    return {
      utm_source: el.getAttribute("data-utm-source") || "landing",
      utm_medium: el.getAttribute("data-utm-medium") || "cta",
      utm_campaign: el.getAttribute("data-utm-campaign") || "store",
    };
  }

  var script = document.createElement("script");
  script.src = "https://cdn.paddle.com/paddle/v2/paddle.js";
  script.async = true;

  script.onload = function () {
    var Paddle = window.Paddle;
    if (!Paddle || !Paddle.Initialize) return; // stay plain links

    if (cfg.mode === "sandbox") Paddle.Environment.set("sandbox");

    Paddle.Initialize({
      token: cfg.clientToken,
      eventCallback: function (ev) {
        if (ev && ev.name === "checkout.completed") {
          var next = document.getElementById("purchase-next");
          if (next) next.hidden = false;
          var first = ctas[0];
          if (first.focus) first.focus({ preventScroll: true });
        }
      },
    });

    ctas.forEach(function (cta) {
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
          customData: utmData(cta),
        });
      });
    });
  };

  document.head.appendChild(script);
})();
