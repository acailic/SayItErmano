/* Paddle overlay checkout (#17) — progressive enhancement.

   Until the constants in paddle-config.js are filled in, the buy button
   keeps its static fallback (the reserve thread). Once configured, the
   button opens the Paddle overlay and its href becomes the hosted
   payment link with UTM ids (the measurement plan, #18:
   utm_source=landing&utm_medium=cta&utm_campaign=store).

   Paddle.js is loaded only after the visitor clicks buy: no third-party
   script, cookies or requests until a checkout is actually opened —
   which keeps the site's "no tracking, no cookies" claim true.
*/
(function () {
  "use strict";

  var cfg = window.SAYIT_PADDLE;
  var buttons = Array.prototype.slice.call(
    document.querySelectorAll("[data-paddle-buy]")
  );
  if (!buttons.length) return;

  var FALLBACK_TEXT = "Reserve $39 intro — checkout opens at launch";
  var BUY_TEXT = "Buy now — $39 intro price";
  var UTM = "utm_source=landing&utm_medium=cta&utm_campaign=store";
  var CDN = "https://cdn.paddle.com/paddle/v2/paddle.js";

  var configured = cfg && cfg.clientToken && cfg.priceId;

  if (!configured) {
    buttons.forEach(function (btn) { btn.textContent = FALLBACK_TEXT; });
    return;
  }

  var paddleReady = null;

  function initPaddle() {
    var P = window.Paddle;
    if (cfg.mode === "sandbox") P.Environment.set("sandbox");
    P.Initialize({ token: cfg.clientToken });
    return P;
  }

  function loadPaddle() {
    if (!paddleReady) {
      paddleReady = new Promise(function (resolve, reject) {
        var s = document.createElement("script");
        s.src = CDN;
        s.async = true;
        s.onload = function () {
          try { resolve(initPaddle()); }
          catch (err) { reject(err); }
        };
        s.onerror = function () {
          paddleReady = null;
          reject(new Error("paddle.js failed to load"));
        };
        document.head.appendChild(s);
      });
    }
    return paddleReady;
  }

  buttons.forEach(function (btn) {
    btn.textContent = BUY_TEXT;
    if (cfg.paymentLink) {
      var join = cfg.paymentLink.indexOf("?") === -1 ? "?" : "&";
      btn.href = cfg.paymentLink + join + UTM;
    }
    btn.addEventListener("click", function (e) {
      if (e.defaultPrevented) return;
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      loadPaddle().then(function (P) {
        P.Checkout.open({
          items: [{ priceId: cfg.priceId, quantity: 1 }],
          settings: { theme: "dark", allowLogout: false },
          customData: {
            utm_source: "landing",
            utm_medium: "cta",
            utm_campaign: "store"
          }
        });
      }).catch(function () {
        window.location.href = btn.href;
      });
    });
  });
})();
