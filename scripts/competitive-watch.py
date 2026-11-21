#!/usr/bin/env python3
"""Competitive watch for SayItErmano (issue #25, ADR-0005 revisit triggers).

Two check tiers, both network-read-only and dependency-free:

  weekly   (scriptable): GitHub repos (stars/pushed_at) + Flathub search
                         installs for the four discovery queries
  monthly  (pages):      pricing/status pages, change-detected by
                         sha256+length against the saved state — no
                         content interpretation, just "it changed"

Alert triggers (printed in the digest header when they fire):
  - Wispr Flow drops the "no native Linux application" line from its
    supported-devices doc  ->  positioning flip (see ADR-0005)
  - a watched repo's stars jump > +25% week-over-week
  - any watched pricing page changes

Usage:
  scripts/competitive-watch.py [--state PATH] [--out PATH] [--quiet]

State defaults to ~/.cache/sayitermano-watch/state.json; digests are
markdown on stdout (or --out). Exit 0 always — this is a reporter, not
a gate.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import urllib.request
from pathlib import Path

GITHUB_REPOS = [
    "cjpais/handy",               # free MIT baseline (39k stars at launch)
    "openwhispr/openwhispr",      # free local + $80/yr pro
    "VocaHQ/vocalinux",           # free AGPL, flathub-rejected precedent
    "peteonrails/voxtype",        # now free MIT
    "wispr-flow-linux/wispr-flow-linux",  # unofficial port = demand canary
]

FLATHUB_QUERIES = ["dictation", "voice typing", "speech to text", "whisper"]

MONTHLY_PAGES = {
    "Wispr pricing": "https://wisprflow.ai/pricing",
    "Wispr downloads (Linux waitlist)": "https://wisprflow.ai/downloads",
    "Wispr supported devices": "https://docs.wisprflow.ai/articles/6478598909",
    "Voicy pricing": "https://usevoicy.com/pricing/",
    "Vibe Typer pricing": "https://vibetyper.com/pricing",
    "OpenWhispr pricing": "https://openwhispr.com/pricing",
    "MacWhisper": "https://macwhisper.com",
    "Voxtty": "https://voxtty.com",
    "Blip AI": "https://blipai.app",
}

UA = {"User-Agent": "sayitermano-competitive-watch/1.0 (+github.com/acailic/SayItErmano)"}
STAR_JUMP = 1.25  # +25% week-over-week flags an alert

DEFAULT_STATE = Path("~/.cache/sayitermano-watch/state.json").expanduser()


def _fetch(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _fetch_json(url: str, data: bytes | None = None):
    if data is not None:
        req = urllib.request.Request(url, data=data, headers={**UA,
                               "Content-Type": "application/json"}, method="POST")
    else:
        req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def check_github() -> list[dict]:
    rows = []
    for slug in GITHUB_REPOS:
        try:
            j = _fetch_json(f"https://api.github.com/repos/{slug}")
            rows.append({"repo": slug, "stars": j.get("stargazers_count"),
                         "pushed": (j.get("pushed_at") or "")[:10]})
        except Exception as e:  # noqa: BLE001 - reporter, not a gate
            rows.append({"repo": slug, "error": str(e)[:120]})
    return rows


def check_flathub() -> dict[str, list[dict]]:
    out = {}
    for q in FLATHUB_QUERIES:
        try:
            j = _fetch_json("https://flathub.org/api/v2/search",
                            json.dumps({"query": q}).encode())
            hits = j.get("hits", [])[:5]
            out[q] = [{"app": h.get("flatpakAppId") or h.get("name"),
                       "installs_last_month": h.get("installs_last_month")}
                      for h in hits]
        except Exception as e:  # noqa: BLE001
            out[q] = [{"error": str(e)[:120]}]
    return out


def _page_signature(body: bytes) -> str:
    """Content signature that ignores churn (scripts, styles, timestamps).

    Raw sha256 of fetched pages cries wolf weekly — same-length
    different-hash diffs are CSRF tokens and cache-busters, not content.
    Strip script/style blocks, long digit runs and long hex tokens, then
    hash the surviving text.
    """
    import re
    t = body.decode("utf-8", "replace")
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", t)
    t = re.sub(r"[0-9a-f]{20,}", "", t)
    t = re.sub(r"\d{7,}", "", t)
    t = re.sub(r"\s+", " ", t)
    return f"{hashlib.sha256(t.encode('utf-8')).hexdigest()[:16]}… {len(t)}c"


def check_pages() -> dict[str, str]:
    """Signature per page, so change detection needs no interpretation."""
    out = {}
    for name, url in MONTHLY_PAGES.items():
        try:
            out[name] = _page_signature(_fetch(url))
        except Exception as e:  # noqa: BLE001
            out[name] = f"ERROR {str(e)[:80]}"
    return out


def check_wispr_linux() -> str:
    """Two independent signals; alert only when BOTH flip.

    The docs article's exact wording has changed before (\"no native
    Linux application\" -> \"Linux … are not supported\"), so a single
    phrase is a false-alarm generator. Linux is 'still not shipped' iff:
      - the supported-devices doc still lists Linux as not supported, OR
      - the downloads page still shows the vote-for-Linux waitlist.
    """
    import re
    ok_signals = 0
    notes = []
    try:
        devices = _fetch("https://docs.wisprflow.ai/articles/1036674442") \
            .decode("utf-8", "replace")
        if re.search(r"[Ll]inux[^.]{0,120}not supported", devices) \
                or re.search(r"not supported[^.]{0,120}[Ll]inux", devices):
            ok_signals += 1
            notes.append("docs: Linux still in the not-supported list")
        else:
            notes.append("docs: Linux MISSING from the not-supported list — verify")
    except Exception as e:  # noqa: BLE001
        notes.append(f"docs unverifiable: {str(e)[:60]}")
    try:
        downloads = _fetch("https://wisprflow.ai/downloads") \
            .decode("utf-8", "replace")
        if re.search(r"[Vv]ote[^.]{0,80}[Ll]inux", downloads) \
                or re.search(r"[Ll]inux[^.]{0,80}[Vv]ote", downloads):
            ok_signals += 1
            notes.append("downloads: vote-for-Linux waitlist still present")
        else:
            notes.append("downloads: no vote-for-Linux text — verify a real download")
    except Exception as e:  # noqa: BLE001
        notes.append(f"downloads unverifiable: {str(e)[:60]}")
    if ok_signals == 0:
        return "ALERT: both Linux-absent signals flipped — Wispr may have shipped Linux. Verify manually now."
    return "still no Linux (" + "; ".join(notes) + ")"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    prev = {}
    if args.state.exists():
        try:
            prev = json.loads(args.state.read_text())
        except (OSError, json.JSONDecodeError):
            prev = {}

    gh = check_github()
    fh = check_flathub()
    pages = check_pages()
    wispr = check_wispr_linux()

    alerts = []
    if wispr.startswith("ALERT"):
        alerts.append(f"Wispr Linux: {wispr}")
    prev_gh = {r["repo"]: r for r in prev.get("github", []) if "stars" in r}
    for r in gh:
        if "stars" in r and r["repo"] in prev_gh:
            before = prev_gh[r["repo"]]["stars"] or 0
            if before and (r["stars"] or 0) >= before * STAR_JUMP:
                alerts.append(f"{r['repo']}: {before} -> {r['stars']} stars")
    prev_pages = prev.get("pages", {})
    for name, sig in pages.items():
        if name in prev_pages and prev_pages[name] != sig \
                and not sig.startswith("ERROR") and not prev_pages[name].startswith("ERROR"):
            alerts.append(f"page changed: {name} ({prev_pages[name]} -> {sig})")

    lines = [f"# Competitive watch digest — {today}", ""]
    if alerts:
        lines += ["## ALERTS", ""] + [f"- **{a}**" for a in alerts] + [""]
    lines += ["## GitHub (weekly tier)", "",
              "| repo | stars | last push |", "|---|---|---|"]
    for r in gh:
        lines.append(f"| {r['repo']} | {r.get('stars', r.get('error', '?'))} "
                     f"| {r.get('pushed', '')} |")
    lines += ["", "## Flathub installs_last_month (top 5 per query)", ""]
    for q, hits in fh.items():
        lines.append(f"**{q}**: " + "; ".join(
            f"{h['app']}={h.get('installs_last_month', h.get('error', '?'))}"
            for h in hits))
    lines += ["", "## Pricing/status pages (monthly tier, change by signature)", ""]
    for name, sig in pages.items():
        changed = " (CHANGED since last run)" if name in prev_pages \
            and prev_pages[name] != sig else ""
        lines.append(f"- {name}: {sig}{changed}")
    lines += ["", "## Wispr Linux signal", "", wispr, "",
              f"_State: {args.state}; run weekly (script tier) and monthly "
              f"(pages tier) per issue #25._"]
    digest = "\n".join(lines)

    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(
        {"date": today, "github": gh, "pages": pages}, indent=1))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(digest + "\n")
        if not args.quiet:
            print(f"digest written: {args.out}")
    elif not args.quiet:
        print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
