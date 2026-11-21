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
- `assets/img/` — web-optimized copies of `docs/screenshots/`
  (compressed/resized for the page), the favicon set generated from
  `fluidvoice/assets/icon.png`, and the typographic `og-image.png`.
  If a screenshot changes upstream, regenerate with the same
  Pillow recipe (resize + `optimize=True`, GIF ≤ 800 px wide).

## Store checkout (#17)

- `assets/js/store-config.js` — **the one constant file**: Paddle
  client-side token + price id per environment, and the `mode` flag.
  It ships `"sandbox"` with placeholder ids; go-live = create the
  live product, fill the `live` block, flip `mode: "live"`. Values
  containing `REPLACE` make the page degrade honestly (plain link,
  explanatory message) instead of opening a broken checkout.
- `assets/js/store.js` — Paddle.js **v2** (Paddle Billing) overlay
  checkout. Loads `https://cdn.paddle.com/paddle/v2/paddle.js`
  lazily on the first buy click, so browsing the site makes no
  third-party request; with JS off, the CTA is still a plain link to
  `buy.html`. Buy CTAs are anchors with `data-store-buy`; their
  `utm_*` query params ride along as Paddle `customData` so
  transactions carry their source (measurement plan #18:
  `utm_source=landing&utm_medium=cta&utm_campaign=store`).
- `buy.html` — checkout fallback page and the **Paddle default
  payment link** target (set it in Paddle > Checkout > Checkout
  configuration — sandbox and live separately; `?_ptxn=` transaction
  payment links open on it). Registered in the dashboard, not here.
- Comparison table on `index.html`: numbers only from
  [docs/PRICING.md](../docs/PRICING.md)'s verified set, with the
  check date printed in the table header; `tests/test_site_links.py`
  pins the presence of the date and competitors.

Because the site is served under the repo subpath `/SayItErmano/`,
**all asset URLs must stay relative** — no leading-slash paths.

Check an edit locally:

```bash
cd site && python3 -m http.server 8000   # then open http://localhost:8000
```

Static gate (also run by the unit tier):
`.venv/bin/python -m pytest tests/test_site_links.py -q` — buy CTA
present with UTM tags, legal/store pages exist, no dead local
hrefs/srcs, comparison table carries the verified date.

Note: `scripts/check_docs_links.py` covers `docs/`, not this tree.
