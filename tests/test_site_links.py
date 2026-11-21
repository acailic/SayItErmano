"""Landing-site static gate (#17).

The site/ tree has no build step and no framework, so its invariants
are checked by parsing the shipped HTML directly — stdlib only, no
network, no Paddle calls (the store is exercised in sandbox by the
owner runbooks, not by the suite):

- every local href/src in site/*.html resolves to an existing file;
- same-page fragment targets (#foo) have a matching id;
- the legal pages exist and are linked from every page footer;
- the buy CTA exists, keeps a real no-JS fallback href, and carries
  the utm data tags from the funnel decision rule
  (docs/pricing-funnel-decision-rule.md);
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
# the store CTA (#17 + #18 tags)
# ---------------------------------------------------------------------------

def _buy_cta() -> tuple[dict[str, str], str]:
    text = (SITE / "index.html").read_text(encoding="utf-8")

    class CTA(HTMLParser):
        attrs: dict[str, str] = {}
        text_inside = ""

        def handle_starttag(self, tag, attrs):
            if any(k == "id" and v == "buy-cta" for k, v in attrs):
                self.attrs = {k: v or "" for k, v in attrs}

        def handle_data(self, data):
            if self.attrs:
                self.text_inside += data.strip()

    cta = CTA()
    cta.feed(text)
    return cta.attrs, cta.text_inside


def test_buy_cta_present_with_fallback_and_utm_tags() -> None:
    attrs, label = _buy_cta()
    assert attrs, "index.html has no element id=buy-cta"
    href = attrs.get("href", "")
    assert href.startswith("https://"), (
        f"buy CTA needs an https no-JS fallback href, got {href!r}")
    for key, value in LANDING_UTM.items():
        assert attrs.get(key) == value, (
            f"buy CTA {key} must be {value!r} (funnel decision rule), "
            f"got {attrs.get(key)!r}")
    assert label, "buy CTA label text is empty"


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
    # the loader must be behind the armed check, not top-level
    armed_pos = js.find("if (!armed) return")
    load_pos = js.find("createElement")
    assert 0 < armed_pos < load_pos
