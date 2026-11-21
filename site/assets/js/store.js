/* SayItErmano store — Paddle.js v2 overlay checkout (#17).

   Progressive enhancement, same rule as site.js: with this script
   absent or blocked, the buy CTA is still a plain link to buy.html
   (which statically includes Paddle.js). Paddle's script is loaded
   LAZILY, on the first buy click — the landing page itself makes no
   third-party request while browsing (footer claim: no tracking, no
   cookies).

   Verified against developer.paddle.com (2026-11-21):
   - CDN: https://cdn.paddle.com/paddle/v2/paddle.js
   - sandbox: Paddle.Environment.set("sandbox") before Initialize
   - Paddle.Initialize({ token, checkout: { settings } })
   - Paddle.Checkout.open({ items: [{priceId, quantity}], customData })
   - customData carries utm_* onto the transaction (attribution).
*/
(function () {
  "use strict";

  var CFG = window.SAYIT_STORE;
  if (!CFG) return; /* config must load first */

  var CDN = "https://cdn.paddle.com/paddle/v2/paddle.js";
  var PLACEHOLDER = /REPLACE/;

  function env() {
    return CFG.mode === "live" ? CFG.live : CFG.sandbox;
  }

  function configured() {
    var e = env();
    return !PLACEHOLDER.test(e.clientToken) && !PLACEHOLDER.test(e.priceId);
  }

  /* -- lazy Paddle.js loader ------------------------------------------ */
  var loading = null;

  function loadPaddle() {
    if (window.Paddle) return Promise.resolve(window.Paddle);
    if (loading) return loading;
    loading = new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = CDN;
      s.async = true;
      s.onload = function () {
        window.Paddle ? resolve(window.Paddle)
                      : reject(new Error("Paddle.js loaded but unavailable"));
      };
      s.onerror = function () {
        loading = null;
        reject(new Error("Paddle.js failed to load"));
      };
      document.head.appendChild(s);
    });
    return loading;
  }

  function initPaddle(P) {
    if (CFG.mode !== "live") P.Environment.set("sandbox");
    P.Initialize({
      token: env().clientToken,
      checkout: { settings: CFG.checkout },
    });
    return P;
  }

  function openCheckout(P, attributionOverride) {
    P.Checkout.open({
      items: [{ priceId: env().priceId, quantity: 1 }],
      customData: attributionOverride || CFG.attribution,
      settings: CFG.checkout,
    });
  }

  /* attribution for a link = its own utm_* query params when present
     (README/Flathub get their own tags per the measurement plan),
     else the config default. */
  function attributionFrom(url) {
    var out = {};
    var q = (url || "").split("?")[1];
    if (!q) return null;
    q.split("&").forEach(function (pair) {
      var kv = pair.split("=");
      if (/^utm_/.test(kv[0]) && kv[1]) out[kv[0]] = decodeURIComponent(kv[1] || "");
    });
    return Object.keys(out).length ? out : null;
  }

  /* -- buy CTAs: <a data-store-buy href="buy.html?utm_..."> ---------- */
  function wire() {
    var links = document.querySelectorAll("[data-store-buy]");

    if (CFG.mode !== "live") {
      var note = document.getElementById("store-mode-note");
      if (note) note.removeAttribute("hidden");
    }

    if (!links.length) return;

    if (!configured()) {
      /* ids not provisioned yet — leave the plain link to buy.html,
         which explains the state honestly. */
      return;
    }

    Array.prototype.forEach.call(links, function (a) {
      a.addEventListener("click", function (ev) {
        if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
        ev.preventDefault();
        loadPaddle()
          .then(initPaddle)
          .then(function (P) { openCheckout(P, attributionFrom(a.href)); })
          .catch(function () { window.location.href = a.href; /* fall back to buy.html */ });
      });
    });
  }

  /* -- buy.html: auto-open on load (also the Paddle default payment
       link page: transaction payment links hit it with ?_ptxn=...,
       which Paddle.js itself handles once initialized) --------------- */
  function autoOpen() {
    var host = document.querySelector("[data-store-page]");
    if (!host) return;

    function unavailable(reason) {
      var msg = host.querySelector("[data-store-status]");
      if (msg) {
        msg.removeAttribute("hidden");
        if (reason && msg.querySelector("[data-store-reason]")) {
          msg.querySelector("[data-store-reason]").textContent = " (" + reason + ")";
        }
      }
    }

    if (!configured()) {
      unavailable("");
      return;
    }
    var status = host.querySelector("[data-store-loading]");
    if (status) status.removeAttribute("hidden");

    loadPaddle()
      .then(initPaddle)
      .then(function (P) {
        if (status) status.setAttribute("hidden", "");
        /* buy.html opened as a plain fallback (no _ptxn): open the
           supporter-license checkout ourselves; attribution comes
           from this page's own utm_* params. */
        if (!/[?&]_ptxn=/.test(window.location.search)) {
          openCheckout(P, attributionFrom(window.location.href));
        }
      })
      .catch(function (err) { unavailable(String(err && err.message || err)); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { wire(); autoOpen(); });
  } else {
    wire();
    autoOpen();
  }
})();
