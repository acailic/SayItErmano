/* SayItErmano store config (#17) — the ONE file flipped at go-live.
 *
 * With empty id fields (store not provisioned yet) the buy CTA stays a
 * plain link to `fallbackUrl` and nothing at all is loaded from
 * paddle.com. The moment a client token + price id are pasted in, the
 * Paddle.js overlay checkout arms (assets/js/store.js).
 *
 * Where the values come from (Paddle dashboard):
 *   clientToken  My account > Settings > Authentication > client-side
 *                tokens — "test_..." while mode is "sandbox",
 *                "live_..." for production
 *   priceId      Catalog > supporter-license price ("pri_...")
 *   productId    Catalog > supporter-license product ("pro_...") — the
 *                same id must be a key in the webhook's product map
 *                (docs/dev/license-ops.md) or purchases never mint keys
 *
 * Go-live flip (full checklist in site/README.md):
 *   1. set mode to "live", replace the three ids with production ones;
 *   2. point fallbackUrl at the hosted checkout URL, keeping the tags
 *      utm_source=landing&utm_medium=cta&utm_campaign=store
 *      (docs/pricing-funnel-decision-rule.md).
 */
window.SAYIT_STORE = {
  mode: "sandbox", // "sandbox" | "live" — sandbox before Paddle seller approval
  clientToken: "", // sandbox client-side token goes in first
  priceId: "", // sandbox price id goes in first
  productId: "", // sandbox product id goes in first
  fallbackUrl: "https://github.com/acailic/SayItErmano/discussions/35?utm_source=landing&utm_medium=cta&utm_campaign=store",
};
