# Landing site

The public landing page, served at
**https://acailic.github.io/SayItErmano/** by
[`.github/workflows/pages.yml`](../.github/workflows/pages.yml)
(GitHub Pages, `build_type: workflow`; the artifact is this directory
verbatim — there is no build step).

- `index.html` — the whole site, hand-crafted, no framework. Every
  factual claim traces to the README, CHANGELOG, or `docs/`; keep the
  "unofficial community port of FluidVoice, not affiliated"
  attribution visible.
- `assets/css/site.css`, `assets/js/site.js` — the design system
  (Geist + Geist Mono, bundled under `assets/fonts/` per the SIL OFL
  1.1 — license file alongside; no CDN). JS is progressive
  enhancement: the page is fully readable with it disabled, and
  motion respects `prefers-reduced-motion`.
- `assets/js/store-config.js` + `assets/js/store.js` — the Paddle
  overlay checkout (#17). `store-config.js` is the ONE go-live
  constant: `mode` ("sandbox" first, "live" at launch) plus the
  Paddle client token / price id / product id. With empty ids the buy
  CTA stays a plain link to `fallbackUrl` and nothing loads from
  paddle.com; filled ids arm `Paddle.Checkout.open()` with the
  funnel tags (`utm_source=landing&utm_medium=cta&utm_campaign=store`,
  [docs/pricing-funnel-decision-rule.md](../docs/pricing-funnel-decision-rule.md))
  passed as Paddle `customData`. The product id must also be a key in
  the webhook's product map ([docs/dev/license-ops.md](../docs/dev/license-ops.md)).
  Static gate for all of this: `tests/test_site_links.py`.
- `assets/img/` — web-optimized copies of `docs/screenshots/`
  (compressed/resized for the page), the favicon set generated from
  `fluidvoice/assets/icon.png`, and the typographic `og-image.png`.
  If a screenshot changes upstream, regenerate with the same
  Pillow recipe (resize + `optimize=True`, GIF ≤ 800 px wide).
  `assets/img/flathub-badge-en.svg` is the official Flathub badge
  ([source](https://dl.flathub.org/assets/badges/flathub-badge-en.svg)),
  kept local per the no-CDN rule (viewBox added so it scales).

Because the site is served under the repo subpath `/SayItErmano/`,
**all asset URLs must stay relative** — no leading-slash paths.

Check an edit locally:

```bash
cd site && python3 -m http.server 8000   # then open http://localhost:8000
```

Note: `scripts/check_docs_links.py` covers `docs/`, not this tree —
`tests/test_site_links.py` is the site-side gate (dead local links,
buy-CTA tags, store config sanity).

## Closing CTAs and support links (#19)

The bottom CTA section carries the two closing links, both tagged:

- **Buy link** — same contract as the pricing `#buy-cta`: https
  fallback href, `data-utm-*` store tags
  (`utm_source=landing&utm_medium=cta&utm_campaign=store`), price in
  the label, and a `data-store-buy` mark so `store.js` opens the
  Paddle overlay for it too when armed.
- **Flathub badge** (`#flathub-cta`) — the official badge asset,
  served locally. Tag grammar is the decision rule's with its own
  campaign (`utm_source=landing&utm_medium=cta&utm_campaign=flathub`)
  so install clicks never count as store visits in Paddle's
  attribution. The Flathub submission (#12) is still in review, so
  until it merges the badge links the tracking issue and the note
  says so — the listing URL would 404 today.

Support cluster: the FAQ section ends with links to the full FAQ
(`docs/dev/FAQ.md`), SUPPORT.md, the license-keys guide
(`docs/guides/license.md`) and `refund.html`; the footer repeats
Support and License keys. Gate: `tests/test_site_links.py`.

## Go-live checklist (#17)

1. Sandbox first: paste the sandbox client token / price id / product
   id into `assets/js/store-config.js` (`mode: "sandbox"`, ids from
   the Paddle sandbox dashboard) and run the sandbox test purchase —
   key issued, download delivered, VAT invoice on the Paddle receipt
   ([docs/dev/license-ops.md](../docs/dev/license-ops.md),
   [docs/dev/store-emails.md](../docs/dev/store-emails.md),
   [docs/dev/paddle-refunds.md](../docs/dev/paddle-refunds.md)).
2. At launch: flip `mode` to `"live"`, replace the three ids with
   production ones, and point `fallbackUrl` at the hosted checkout
   URL (keep the utm tags). The webhook's product map gains the live
   product id.
3. When the intro cohort closes: update the price card copy in
   `index.html` ($39 intro → $49) and the Paddle price; the
   comparison table and every other number stay sourced from
   `docs/PRICING.md`.
4. When the Flathub submission (#12) merges: point `#flathub-cta` in
   `index.html` at
   `https://flathub.org/apps/io.github.acailic.SayItErmano`
   (keep the utm tags) and drop the "in review" note next to it and
   the Flathub sentence in the install section.
