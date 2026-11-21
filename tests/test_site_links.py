"""Landing-site link gate (#17).

Static, offline parse of site/*.html (stdlib parser only — no network,
no browser): the buy CTA must carry its Paddle wiring and the
measurement-plan UTM tag, legal links must resolve to files, no local
href/src may dangle (Pages ships the tree verbatim under a repo
subpath, so a dead relative link is a broken page), and every
blob/linux doc URL the site points at must exist in-tree.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE = REPO_ROOT / "site"

UTM_RULE = "utm_source=landing&utm_medium=cta&utm_campaign=store"
PADDLE_CDN = "https://cdn.paddle.com/paddle/v2/paddle.js"
BLOB_LINK = re.compile(
    r"https://github\.com/acailic/SayItErmano/blob/linux/([^\s\"'<>)]+)"
)
EXTERNAL = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


class _Page(HTMLParser):
    """Collect href/src refs, element ids, and buy-CTA anchors."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[tuple[str, str]] = []
        self.ids: set[str] = set()
        self.buy_ctas: list[dict[str, str | None]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        seen = dict(attrs)
        for key in ("href", "src"):
            if seen.get(key):
                self.refs.append((key, seen[key]))  # type: ignore[arg-type]
        if seen.get("id"):
            self.ids.add(seen["id"])  # type: ignore[arg-type]
        if "data-paddle-buy" in seen:
            self.buy_ctas.append(seen)


def _page(name: str) -> _Page:
    parser = _Page()
    parser.feed((SITE / name).read_text(encoding="utf-8"))
    parser.close()
    return parser


def _html(name: str) -> str:
    return (SITE / name).read_text(encoding="utf-8")


def _page_names() -> list[str]:
    return [p.name for p in sorted(SITE.glob("*.html"))]


# ---------------------------------------------------------------------------
# buy CTA + Paddle wiring
# ---------------------------------------------------------------------------

def test_buy_cta_present_tagged_with_visible_price() -> None:
    html = _html("index.html")
    parser = _page("index.html")

    assert parser.buy_ctas, "index.html needs a [data-paddle-buy] buy CTA"
    cta = parser.buy_ctas[0]
    assert cta.get("id") == "buy-cta"
    href = cta.get("href")
    assert href and href != "#", "buy CTA needs an href (the no-JS fallback)"

    match = re.search(r"<a[^>]*data-paddle-buy[^>]*>(.*?)</a>", html, re.S)
    assert match, "buy CTA anchor is malformed"
    text = re.sub(r"<[^>]+>", "", match.group(1))
    assert re.search(r"\$\d+", text), "buy CTA must show the price"


def test_paddle_scripts_wired_into_the_page() -> None:
    html = _html("index.html")
    assert 'src="assets/js/paddle-config.js"' in html
    assert 'src="assets/js/paddle-checkout.js"' in html
    assert (SITE / "assets/js/paddle-config.js").is_file()
    assert (SITE / "assets/js/paddle-checkout.js").is_file()


def test_paddle_config_ships_the_flip_constants() -> None:
    js = (SITE / "assets/js/paddle-config.js").read_text(encoding="utf-8")
    assert "window.SAYIT_PADDLE" in js
    assert re.search(r'mode:\s*"(sandbox|live)"', js), "sandbox/live flag"
    for key in ("clientToken", "priceId", "paymentLink"):
        assert re.search(rf"{key}:\s*[\"']", js), f"config key {key} missing"


def test_checkout_carries_the_measurement_utm_rule() -> None:
    js = (SITE / "assets/js/paddle-checkout.js").read_text(encoding="utf-8")
    assert UTM_RULE in js, (
        "every store link must carry the #18 UTM ids "
        "(docs/pricing-funnel-decision-rule.md)"
    )
    assert PADDLE_CDN in js, "overlay checkout must load Paddle.js v2"


# ---------------------------------------------------------------------------
# link resolution across all pages
# ---------------------------------------------------------------------------

def test_local_refs_resolve_and_respect_the_subpath() -> None:
    problems: list[str] = []
    for name in _page_names():
        parser = _page(name)
        for kind, ref in parser.refs:
            if EXTERNAL.match(ref) or ref.startswith("//"):
                continue
            if ref.startswith("#"):
                if ref[1:] and ref[1:] not in parser.ids:
                    problems.append(f"{name}: dangling fragment {ref}")
                continue
            if ref.startswith("/"):
                problems.append(
                    f"{name}: {kind}={ref} must be relative (repo subpath)"
                )
                continue
            target = unquote(ref.split("#", 1)[0].split("?", 1)[0])
            if not target or (SITE / target).is_file():
                continue
            problems.append(f"{name}: dangling local {kind}={ref}")
    assert not problems, "dead local links:\n" + "\n".join(problems)


def test_legal_links_resolve_to_published_pages() -> None:
    legal = ("privacy-policy.html", "terms.html", "refund.html")
    for name in legal:
        assert (SITE / name).is_file(), f"legal page {name} missing"
    parser = _page("index.html")
    footer_legal = {
        ref.split("#")[0].split("?")[0]
        for _, ref in parser.refs
        if not EXTERNAL.match(ref)
    }
    for page in legal:
        assert page in footer_legal, f"index.html must link {page}"


def test_doc_links_point_at_files_in_this_repo() -> None:
    problems: list[str] = []
    for name in _page_names():
        for path in BLOB_LINK.findall(_html(name)):
            if not (REPO_ROOT / unquote(path)).exists():
                problems.append(f"{name}: blob/linux/{path} not in the repo")
    assert not problems, "site points at docs that do not exist:\n" + "\n".join(
        problems
    )


def test_css_urls_resolve() -> None:
    css = (SITE / "assets/css/site.css").read_text(encoding="utf-8")
    for ref in re.findall(r"url\(\s*['\"]?([^'\")]+)", css):
        if EXTERNAL.match(ref) or ref.startswith("data:"):
            continue
        assert (SITE / "assets/css" / unquote(ref)).is_file(), (
            f"site.css url({ref}) does not resolve"
        )
