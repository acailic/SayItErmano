/* SayItErmano store wiring — the ONE constant file (#17).
   Flip `mode` to "live" and fill the live ids at go-live; nothing
   else in the tree hardcodes a Paddle id. Values containing
   "REPLACE" mean "not provisioned yet": store.js treats that as
   checkout-not-configured and shows an honest message instead of a
   broken overlay. Paddle.js v2 (Paddle Billing) — verified against
   developer.paddle.com 2026-11-21. */
window.SAYIT_STORE = {
  mode: "sandbox", /* "sandbox" while testing | "live" at go-live */

  sandbox: {
    clientToken: "test_REPLACE_WITH_SANDBOX_CLIENT_TOKEN", /* Paddle > My account > Settings > Authentication */
    priceId: "pri_REPLACE_WITH_SANDBOX_PRICE_ID",          /* supporter license, sandbox catalog */
  },

  live: {
    clientToken: "live_REPLACE_WITH_LIVE_CLIENT_TOKEN",
    priceId: "pri_REPLACE_WITH_LIVE_PRICE_ID",
  },

  /* Passed to Paddle.Checkout.open() as customData so every
     transaction carries its source (measurement plan #18 — the
     same ids every store link uses in its href). */
  attribution: {
    utm_source: "landing",
    utm_medium: "cta",
    utm_campaign: "store",
  },

  /* Overlay checkout settings (Paddle > dashboard branding still
     applies; these are the JS-side defaults). */
  checkout: {
    displayMode: "overlay",
    theme: "light",
    locale: "en",
  },
};
