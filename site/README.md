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
- `assets/js/paddle-config.js` + `paddle-checkout.js` — the buy flow
  (#17). All Paddle constants live in `paddle-config.js`:
  - until `clientToken`/`priceId` are filled, the buy button shows the
    reserve fallback and links to the launch thread — nothing loads
    from Paddle;
  - once configured, a click loads Paddle.js (sandbox or production
    per `mode`), opens the overlay checkout, and the button's href
    becomes the hosted `paymentLink` tagged with the measurement-plan
    UTM ids (`utm_source=landing&utm_medium=cta&utm_campaign=store`,
    [docs/pricing-funnel-decision-rule.md](../docs/pricing-funnel-decision-rule.md)).
    Paddle.js loads only on that first click, so the footer's
    "no tracking, no cookies" stays true for everyone who never opens
    the checkout;
  - **go-live flip**: paste the live `clientToken`, `priceId` and
    `paymentLink` from the Paddle dashboard and set `mode: "live"` —
    that one file is the whole flip (sandbox test purchases use the
    sandbox values the same way).
- `assets/img/` — web-optimized copies of `docs/screenshots/`
  (compressed/resized for the page), the favicon set generated from
  `fluidvoice/assets/icon.png`, and the typographic `og-image.png`.
  If a screenshot changes upstream, regenerate with the same
  Pillow recipe (resize + `optimize=True`, GIF ≤ 800 px wide).

Because the site is served under the repo subpath `/SayItErmano/`,
**all asset URLs must stay relative** — no leading-slash paths.

Check an edit locally:

```bash
cd site && python3 -m http.server 8000   # then open http://localhost:8000
```

Note: `scripts/check_docs_links.py` covers `docs/`, not this tree.
