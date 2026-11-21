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
- `assets/js/store.js` — the Paddle checkout wiring (issue #17),
  including the store config block. See below.
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

Note: `scripts/check_docs_links.py` covers `docs/`, not this tree —
the site equivalent is `tests/test_site_links.py`.

## Store wiring (Paddle checkout, issue #17)

`assets/js/store.js` carries the whole store configuration as one
constant block at the top of the file — the site has no build step,
so **that block is the sandbox/live flag**:

- `environment: "sandbox"` — Paddle sandbox (ship state).
- `clientToken` — Paddle client-side token (`test_…` sandbox,
  `live_…` production).
- `priceId` — the supporter-license price id in that environment.

While either id still contains `REPLACE`, buy buttons stay passive
and their `href` fallback (the reserve discussion) keeps working, so
deploying this wiring never breaks the funnel. Going live is one
reviewed edit: paste the live ids, set `environment: "live"`, push.

Paddle.js loads lazily on the first buy click, so the page makes no
third-party request unless a visitor tries to buy — the "no tracking,
no cookies" claim stays true for everyone else. Store links carry the
measurement-plan UTM tags
(`utm_source=landing&utm_medium=cta&utm_campaign=store`, see
`docs/pricing-funnel-decision-rule.md`) as `data-utm-*` attributes
(passed to the checkout as `customData`) and on the fallback href;
`tests/test_site_links.py` pins the wiring.
