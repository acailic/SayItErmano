"""Landing-site static checks (issue #17).

Offline by construction: everything here parses files under site/ —
no HTTP, no Paddle. This is the site/ mirror of the docs link gate
(tests/test_docs_links.py) plus the store wiring that ships with the
Paddle checkout conversion:

- the buy CTA exists, shows the intro price, and carries the
  measurement-plan UTM tags (docs/pricing-funnel-decision-rule.md);
- the legal pages exist and are linked;
- every local href/src resolves, and in-page anchors target real ids;
- the sandbox/live store flag stays coherent with the config block
  in site/assets/js/store.js and is actually included by the page.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "site"
INDEX = SITE / "index.html"
STORE_JS = SITE / "assets" / "js" / "store.js"

UTM_PLAN = {"utm_source": "landing", "utm_medium": "cta", "utm_campaign": "store"}

LEGAL_PAGES = ("privacy-policy.html", "terms.html", "refund.html")

EXTERNAL = ("http://", "https://", "mailto:", "data:")


class _PageParser(HTMLParser):
    """Collect anchors (with inner text of buy CTAs), asset loads, ids."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.anchors: list[dict[str, str]] = []
        self.assets: list[dict[str, str]] = []
        self.buy_cta_text = ""
        self._in_buy_cta = 0
        self._buy_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v if v is not None else "" for k, v in attrs}
        if a.get("id"):
            self.ids.add(a["id"])
        if "href" in a or "src" in a:
            (self.anchors if tag == "a" else self.assets).append(a)
        if tag == "a" and "data-paddle-checkout" in a:
            self._in_buy_cta += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_buy_cta:
            self._in_buy_cta -= 1
            if not self._in_buy_cta:
                self.buy_cta_text = "".join(self._buy_buf).strip()
                self._buy_buf = []

    def handle_data(self, data: str) -> None:
        if self._in_buy_cta:
            self._buy_buf.append(data)


class Page:
    """One parsed site/*.html file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        parser = _PageParser()
        parser.feed(path.read_text(encoding="utf-8"))
        self.ids = parser.ids
        self.anchors = parser.anchors
        self.assets = parser.assets
        self.buy_cta_text = parser.buy_cta_text

    def buy_ctas(self) -> list[dict[str, str]]:
        return [a for a in self.anchors if "data-paddle-checkout" in a]

    def __str__(self) -> str:
        return self.path.name


def pages() -> list[Page]:
    return [Page(p) for p in sorted(SITE.glob("*.html"))]


def test_buy_cta_present_priced_and_utm_tagged() -> None:
    page = Page(INDEX)
    ctas = page.buy_ctas()
    assert len(ctas) == 1, "expected exactly one buy CTA on the landing page"
    cta = ctas[0]
    assert cta.get("id") == "buy-cta"
    for key, value in UTM_PLAN.items():
        assert cta.get(f"data-{key.replace('_', '-')}") == value, (
            f"buy CTA missing {key} tag")
        assert f"{key}={value}" in cta.get("href", ""), (
            f"buy CTA fallback href missing {key}={value} query tag")
    assert "$39" in page.buy_cta_text, "buy CTA must show the intro price"


def test_every_store_link_carries_the_utm_tags() -> None:
    for page in pages():
        for cta in page.buy_ctas():
            for key, value in UTM_PLAN.items():
                assert cta.get(f"data-{key.replace('_', '-')}") == value, (
                    f"{page}: store link without {key} tag")


def test_legal_pages_exist_and_are_linked() -> None:
    for name in LEGAL_PAGES:
        assert (SITE / name).is_file(), f"missing legal page {name}"
    index = Page(INDEX)
    for name in LEGAL_PAGES:
        assert any(a.get("href") == name for a in index.anchors), (
            f"index.html does not link {name}")


def test_refund_page_links_the_runbook() -> None:
    refund = Page(SITE / "refund.html")
    assert any("docs/dev/paddle-refunds.md" in a.get("href", "")
               for a in refund.anchors), "refund page must link the ops runbook"


def test_no_dead_local_hrefs_or_asset_sources() -> None:
    problems: list[str] = []
    for page in pages():
        for a in page.anchors + page.assets:
            for attr in ("href", "src"):
                url = a.get(attr, "")
                if not url or url.startswith(EXTERNAL):
                    continue
                if url.startswith("#"):
                    if url[1:] and url[1:] not in page.ids:
                        problems.append(f"{page}: dangling anchor {url}")
                    continue
                target = SITE / url.split("#", 1)[0].split("?", 1)[0]
                if not target.is_file():
                    problems.append(f"{page}: {url} is not a local file")
    assert not problems, "dead local links:\n" + "\n".join(problems)


def test_privacy_section_sits_above_pricing() -> None:
    html = INDEX.read_text(encoding="utf-8")
    assert html.index('id="privacy"') < html.index('id="pricing"'), (
        "privacy section must stay above the pricing/buy section in the page")


def test_store_config_flag_and_wiring() -> None:
    js = STORE_JS.read_text(encoding="utf-8")
    assert re.search(r'environment:\s*"(sandbox|live)"', js), (
        "store.js must set the environment flag")
    for key in ("clientToken", "priceId"):
        assert re.search(rf'{key}:\s*"', js), f"store.js missing {key} constant"
    assert "cdn.paddle.com/paddle/v2/paddle.js" in js, (
        "store.js must load the official Paddle.js CDN script")
    assert 'src="assets/js/store.js"' in INDEX.read_text(encoding="utf-8"), (
        "index.html must include store.js")
