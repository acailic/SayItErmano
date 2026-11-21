"""Landing-site static gate (#17, extended by #19).

The site/ tree has no build step and no framework, so its invariants
are checked by parsing the shipped HTML directly — stdlib only, no
network, no Paddle calls (the store is exercised in sandbox by the
owner runbooks, not by the suite):

- every local href/src in site/*.html resolves to an existing file;
- same-page fragment targets (#foo) have a matching id;
- the legal pages exist and are linked from every page footer;
- every buy CTA (pricing card + closing section, #19) keeps a real
  no-JS fallback href, shows the price, and carries the utm data
  tags from the funnel decision rule
  (docs/pricing-funnel-decision-rule.md);
- the Flathub badge CTA exists with the official badge asset served
  locally and its own landing attribution tags
  (utm_campaign=flathub — never the store campaign, so Paddle
  attribution stays clean);
- the support cluster is reachable from the landing page:
  SUPPORT.md, the license-keys guide, the full FAQ, refund policy;
- the store config defines the sandbox/live flip constants;
- the privacy section stays above the pricing section on the landing
  page (the store must not bury the local-audio promise).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "site"

LEGAL_PAGES = ("privacy-policy.html", "terms.html", "refund.html")
EXTERNAL = ("http://", "https://", "mailto:", "data:", "//")
# docs/pricing-funnel-decision-rule.md — the landing-site store tag
LANDING_UTM = {"data-utm-source": "landing",
               "data-utm-medium": "cta",
               "data-utm-campaign": "store"}
# the install-side sibling of the store tag (site/README.md): same
# grammar, its own campaign, so install clicks never masquerade as
# store visits in Paddle's attribution
FLATHUB_UTM = {"data-utm-source": "landing",
               "data-utm-medium": "cta",
               "data-utm-campaign": "flathub"}
FLATHUB_BADGE_ASSET = "assets/img/flathub-badge-en.svg"
SUPPORT_LINKS = (
    "https://github.com/acailic/SayItErmano/blob/linux/SUPPORT.md",
    "https://github.com/acailic/SayItErmano/blob/linux/docs/guides/license.md",
    "https://github.com/acailic/SayItErmano/blob/linux/docs/dev/FAQ.md",
    "refund.html",
)
STORE_CONFIG_KEYS = ("mode", "clientToken", "priceId", "productId",
                     "fallbackUrl")


class _Collector(HTMLParser):
    """Grabs every href/src attribute and every id= in one pass."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrd = {k: v for k, v in attrs if v is not None}
        for key in ("href", "src"):
            if key in attrd:
                self.urls.append(attrd[key])
        if "id" in attrd:
            self.ids.add(attrd["id"])


def _parse(path: Path) -> _Collector:
    c = _Collector()
    c.feed(path.read_text(encoding="utf-8"))
    c.close()
    return c


def _index() -> str:
    return (SITE / "index.html").read_text(encoding="utf-8")


def _anchors(html: str, want) -> list[dict[str, str]]:
    """Anchors whose attrs satisfy `want`, as attr dicts plus `_text`
    (label) and `_img_src`/`_img_alt` when the anchor wraps an image.
    Nested anchors are invalid HTML and not expected."""
    class P(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.found: list[dict[str, str]] = []
            self._cur: dict[str, str] | None = None
            self._buf: list[str] = []
            self._img: dict[str, str] | None = None

        def handle_starttag(self, tag, attrs):
            attrd = {k: v or "" for k, v in attrs}
            if self._cur is None:
                if tag == "a" and want(attrd):
                    self._cur = attrd
                    self._buf = []
                    self._img = None
            elif tag == "img":
                self._img = attrd

        def handle_endtag(self, tag):
            if tag == "a" and self._cur is not None:
                el = dict(self._cur)
                el["_text"] = " ".join(" ".join(self._buf).split())
                if self._img:
                    el["_img_src"] = self._img.get("src", "")
                    el["_img_alt"] = self._img.get("alt", "")
                self.found.append(el)
                self._cur = None

        def handle_data(self, data):
            if self._cur is not None and data.strip():
                self._buf.append(data.strip())

    p = P()
    p.feed(html)
    p.close()
    return p.found


def _html_files() -> list[Path]:
    return sorted(SITE.glob("*.html"))


# ---------------------------------------------------------------------------
# local links resolve
# ---------------------------------------------------------------------------

def test_no_dead_local_links() -> None:
    problems: list[str] = []
    for page in _html_files():
        for url in _parse(page).urls:
            if not url or url.startswith(EXTERNAL):
                continue
            if url.startswith("#"):
                continue  # fragments: covered by their own test
            target = (page.parent / url.partition("#")[0]).resolve()
            if not target.exists():
                problems.append(f"{page.name}: {url}")
    assert not problems, "dead local links:\n" + "\n".join(problems)


def test_fragment_targets_have_ids() -> None:
    problems: list[str] = []
    for page in _html_files():
        c = _parse(page)
        for url in c.urls:
            if url.startswith("#") and url != "#":
                if url[1:] not in c.ids:
                    problems.append(f"{page.name}: {url}")
    assert not problems, "dangling same-page fragments:\n" + "\n".join(problems)


def test_legal_pages_exist_and_are_linked() -> None:
    for name in LEGAL_PAGES:
        assert (SITE / name).is_file(), f"legal page missing: {name}"
    for page in _html_files():
        urls = _parse(page).urls
        # a page never needs to link itself; everything else must be
        # reachable from everywhere (Paddle seller-review readiness)
        needed = [n for n in LEGAL_PAGES if n != page.name]
        missing = [n for n in needed if n not in urls]
        assert not missing, (f"{page.name} does not link the legal "
                             f"page(s): {', '.join(missing)}")


# ---------------------------------------------------------------------------
# the store CTAs (#17 + #18 tags; closing link added by #19)
# ---------------------------------------------------------------------------

def _buy_ctas() -> list[dict[str, str]]:
    return _anchors(_index(),
                    lambda a: a.get("id") == "buy-cta"
                    or "data-store-buy" in a)


def test_buy_ctas_present_with_price_fallback_and_utm_tags() -> None:
    ctas = _buy_ctas()
    assert len(ctas) >= 2, (
        "index.html needs the pricing buy CTA (id=buy-cta) and the "
        "closing-section buy link (data-store-buy)")
    for el in ctas:
        href = el.get("href", "")
        assert href.startswith("https://"), (
            f"buy CTA {el.get('id', 'closing')!r} needs an https no-JS "
            f"fallback href, got {href!r}")
        for key, value in LANDING_UTM.items():
            assert el.get(key) == value, (
                f"buy CTA {key} must be {value!r} (funnel decision rule), "
                f"got {el.get(key)!r}")
        assert el["_text"], "buy CTA label text is empty"
        assert re.search(r"\$\d+", el["_text"]), (
            f"buy CTA label must show the price, got {el['_text']!r}")


def test_flathub_badge_cta_present_tagged_and_local() -> None:
    assert (SITE / FLATHUB_BADGE_ASSET).is_file(), (
        f"the official Flathub badge must ship locally: {FLATHUB_BADGE_ASSET}")
    ctas = _anchors(_index(), lambda a: a.get("id") == "flathub-cta")
    assert len(ctas) == 1, "index.html needs exactly one id=flathub-cta"
    el = ctas[0]
    assert el.get("href", "").startswith("https://"), (
        f"Flathub badge needs an https href, got {el.get('href')!r}")
    for key, value in FLATHUB_UTM.items():
        assert el.get(key) == value, (
            f"Flathub badge {key} must be {value!r}, got {el.get(key)!r}")
    assert el.get("_img_src") == FLATHUB_BADGE_ASSET, (
        "Flathub badge must use the local asset copy, no CDN")
    assert (SITE / el["_img_src"]).is_file()
    assert el["_img_alt"].strip(), "Flathub badge image needs alt text"


def test_support_links_present() -> None:
    urls = _parse(SITE / "index.html").urls
    for url in SUPPORT_LINKS:
        assert url in urls, f"index.html must link the support route {url}"


def test_store_config_defines_flip_constants() -> None:
    cfg = SITE / "assets" / "js" / "store-config.js"
    assert cfg.is_file(), "site/assets/js/store-config.js is missing"
    text = cfg.read_text(encoding="utf-8")
    for key in STORE_CONFIG_KEYS:
        assert re.search(rf"\b{key}\s*:", text), f"store config: no {key}"
    m = re.search(r'mode:\s*"(sandbox|live)"', text)
    assert m, 'store config: mode must be "sandbox" or "live"'
    assert "cdn.paddle.com" not in text  # ids only — URLs live in store.js


def test_privacy_section_stays_above_pricing() -> None:
    text = (SITE / "index.html").read_text(encoding="utf-8")
    privacy = text.find('id="privacy"')
    pricing = text.find('id="pricing"')
    assert privacy != -1 and pricing != -1, "privacy/pricing sections missing"
    assert privacy < pricing, (
        "the privacy section must stay above the pricing section")


def test_store_js_only_loads_paddle_when_armed() -> None:
    """Unprovisioned store must not contact paddle.com at all."""
    js = (SITE / "assets" / "js" / "store.js").read_text(encoding="utf-8")
    assert "cdn.paddle.com/paddle/v2/paddle.js" in js
    assert "Environment.set" in js  # sandbox mode honored
    assert "customData" in js      # utm tags reach the transaction
    # every store CTA on the page opens the overlay when armed (#19)
    assert 'querySelectorAll("#buy-cta, [data-store-buy]")' in js
    # the loader must be behind the armed check, not top-level
    armed_pos = js.find("if (!armed) return")
    load_pos = js.find("createElement")
    assert 0 < armed_pos < load_pos
