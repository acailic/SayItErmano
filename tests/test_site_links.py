"""Site link gate — issue #17.

The unit-tier mirror of the landing-site rules in site/README.md:
the buy CTA must exist with its UTM tags (measurement plan #18),
legal/store pages must exist, no local href/src may dangle, the
comparison table must carry the verified date and the competitor
set from #15, and the privacy section must stay above pricing.
Offline by construction: parses files only, never talks to Paddle.
"""

from __future__ import annotations

import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "site"

STORE_UTM = {"utm_source": "landing", "utm_medium": "cta",
             "utm_campaign": "store"}

# #15's verified set (docs/PRICING.md, checked 2026-11-21) — the
# numbers the comparison table must show; drift here means the table
# stopped matching its evidence base.
COMPETITORS = ("Voicy", "MacWhisper", "Vibe Typer", "Wispr Flow")
VERIFIED_NUMBERS = ("$39", "$49", "$260", "€64", "$96/yr", "$15/mo")
VERIFIED_DATE = "2026-11-21"


class _Parser(HTMLParser):
    """Collects links, ids (with document order and text offsets), text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self.ids: set[str] = set()
        self.id_order: list[str] = []
        self.id_offset: dict[str, int] = {}
        self.text = ""
        self._len = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = {k: v or "" for k, v in attrs}
        if d.get("id"):
            self.ids.add(d["id"])
            self.id_order.append(d["id"])
            self.id_offset.setdefault(d["id"], self._len)
        url = d.get("href") or d.get("src")
        if url:
            self.links.append(
                {"tag": tag, "url": url,
                 "rel": d.get("rel", ""), "id": d.get("id", ""),
                 "data-store-buy": "1" if "data-store-buy" in d else ""})

    def handle_data(self, data: str) -> None:
        self._len += len(data)
        self.text += data


class Page:
    def __init__(self, path: Path) -> None:
        self.path = path
        parser = _Parser()
        parser.feed(path.read_text(encoding="utf-8"))
        parser.close()
        self.links = parser.links
        self.ids = parser.ids
        self.id_order = parser.id_order
        self.id_offset = parser.id_offset
        self.text = parser.text

    def text_between(self, start_id: str, end_id: str) -> str:
        """Page text from `start_id`'s element up to `end_id`'s."""
        return self.text[self.id_offset[start_id]:self.id_offset[end_id]]


def _load() -> dict[str, Page]:
    return {p.name: Page(p) for p in sorted(SITE.glob("*.html"))}


def _local_target(url: str) -> Path | None:
    """File a URL points at inside site/, or None when not local."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    return SITE / urllib.parse.unquote(parsed.path)


# -- the buy CTA (#17 + #18) ------------------------------------------

def test_buy_cta_present_with_utm_tags() -> None:
    index = _load()["index.html"]
    ctas = [l for l in index.links if l["data-store-buy"]]
    assert ctas, "no [data-store-buy] buy CTA on index.html"

    assert any(l["id"] == "buy-cta" for l in ctas), \
        "buy CTA lost its id=buy-cta"

    for link in ctas:
        parsed = urllib.parse.urlsplit(link["url"])
        query = urllib.parse.parse_qs(parsed.query)
        for key, expected in STORE_UTM.items():
            assert query.get(key) == [expected], (
                f"{link['url']}: {key} missing/wrong (measurement plan #18)")
        target = _local_target(link["url"])
        assert target is not None and target.name == "buy.html", \
            "buy CTA must fall back to the local buy.html"


def test_reserve_funnel_retired() -> None:
    index = (SITE / "index.html").read_text(encoding="utf-8")
    assert "discussions/35" not in index, (
        "reserve-funnel link still on the page — #17 converts it to buy")


# -- store wiring ------------------------------------------------------

def test_store_scripts_and_config_wired() -> None:
    pages = _load()
    for name in ("index.html", "buy.html"):
        srcs = [l["url"] for l in pages[name].links if l["tag"] == "script"]
        assert "assets/js/store-config.js" in srcs, f"{name}: config not loaded"
        assert "assets/js/store.js" in srcs, f"{name}: loader not loaded"
        assert srcs.index("assets/js/store-config.js") < srcs.index("assets/js/store.js"), \
            f"{name}: store-config.js must load before store.js"

    config = (SITE / "assets" / "js" / "store-config.js").read_text(encoding="utf-8")
    assert 'mode: "sandbox"' in config, "sandbox flag missing from the config"
    assert '"live"' in config and "live: {" in config, \
        'go-live flip (mode: "live" + live ids) not prepared'

    store = (SITE / "assets" / "js" / "store.js").read_text(encoding="utf-8")
    assert "https://cdn.paddle.com/paddle/v2/paddle.js" in store, \
        "Paddle.js v2 CDN URL drifted (Paddle Billing uses /v2/)"
    assert "Environment.set" in store, "sandbox environment switch missing"
    assert "customData" in store, "UTM attribution (customData) missing"


# -- comparison table matches #15's verified set -----------------------

def test_comparison_table_carries_verified_set_and_date() -> None:
    index = _load()["index.html"]
    pricing = index.text_between("pricing-title", "cta-title")
    for who in COMPETITORS:
        assert who in pricing, f"comparison table lost {who}"
    for number in VERIFIED_NUMBERS:
        assert number in pricing, f"verified number {number} missing"
    assert VERIFIED_DATE in pricing, \
        "comparison table must print its verified-on date (#15 evidence rule)"


def test_privacy_section_stays_above_pricing() -> None:
    index = _load()["index.html"]
    assert "privacy" in index.id_order and "pricing" in index.id_order, \
        "privacy/pricing sections lost their anchors"
    assert index.id_order.index("privacy") < index.id_order.index("pricing"), \
        "privacy section must stay above the pricing section (above the fold)"


# -- links resolve -----------------------------------------------------

def test_legal_and_store_pages_exist_and_linked() -> None:
    pages = _load()
    for page in ("privacy-policy.html", "terms.html", "refund.html",
                 "buy.html"):
        assert (SITE / page).exists(), f"{page} missing from site/"
    index = pages["index.html"]
    for page in ("privacy-policy.html", "terms.html", "refund.html"):
        assert any(l["url"].startswith(page) for l in index.links), \
            f"index.html footer no longer links {page}"


def test_no_dead_local_hrefs_or_srcs() -> None:
    problems: list[str] = []
    for name, page in _load().items():
        for link in page.links:
            url = link["url"]
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme in ("mailto", "tel", "data", "javascript"):
                continue
            if parsed.fragment and not parsed.path:
                if parsed.fragment not in page.ids:
                    problems.append(f"{name}: #{parsed.fragment} has no target")
                continue
            target = _local_target(url)
            if target is not None and not target.is_file():
                problems.append(f"{name}: {url} does not resolve to a file")
    assert not problems, "dead local links:\n" + "\n".join(problems)


def test_local_hrefs_stay_relative() -> None:
    # subpath hosting rule (site/README.md): no leading-slash paths
    problems = [f"{name}: {l['url']} is not relative"
                for name, page in _load().items()
                for l in page.links
                if l["url"].startswith("/")]
    assert not problems, "leading-slash local URLs:\n" + "\n".join(problems)
