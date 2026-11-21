#!/usr/bin/env python3
"""Extract translatable strings (_ / _n calls) into po/ catalogs.

Single source of truth for the translation catalogs (like
gen_config_reference.py is for the settings docs):

- AST-walks fluidvoice/ for calls to the i18n accessors _() and _n()
  (constant folding + implicit concatenation; f-string interpolations
  must NOT appear — wrapped strings use .format() with {placeholders}).
- Writes po/sayit-ermano.pot (the template) and merges new/changed
  msgids into every po/<lang>.po, preserving existing translations and
  dropping entries whose msgid disappeared from the code ("orphans").
- Prints a completeness report per language.

Usage:
  python scripts/extract_translations.py            # write/merge + report
  python scripts/extract_translations.py --check    # fail if stale or
                                                    # incomplete (CI gate)

--check exits 1 when: the .pot or any .po would change, a catalog holds
orphaned msgids, or a shipped language has untranslated entries.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fluidvoice.i18n import LANGUAGES  # noqa: E402

PO_DIR = ROOT / "po"
POT = PO_DIR / "sayit-ermano.pot"

ACCESSORS = {"_", "_n"}

#: Plural-Forms header per language (BCP-47 codes as directory names).
PLURAL_FORMS = {
    "sr-Latn": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : "
               "n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
    "sr": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : "
          "n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
    "bs": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : "
          "n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
}

POT_HEADER = """# SayItErmano UI translations — template.
# Regenerate with: python scripts/extract_translations.py
msgid ""
msgstr ""
"Project-Id-Version: SayItErmano\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"""


# -- po file format (minimal reader/writer; no external deps) ----------------

def po_escape(s: str) -> str:
    return (s.replace("\\", "\\\\")
             .replace('"', '\\"')
             .replace("\n", "\\n")
             .replace("\t", "\\t"))


def po_unescape(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n == "n":
                out.append("\n")
            elif n == "t":
                out.append("\t")
            elif n == '"':
                out.append('"')
            elif n == "\\":
                out.append("\\")
            else:
                out.append(n)
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_po(path: Path) -> dict[str, dict]:
    """msgid -> {msgid, msgid_plural, msgstrs: [..], refs: [..]}.
    The header entry (msgid "") is returned under key ''."""
    entries: dict[str, dict] = {}
    if not path.exists():
        return entries
    lines = path.read_text(encoding="utf-8").splitlines()

    def parse_line(line: str):
        """-> (kind, index|None, chunk) for msgid/msgid_plural/msgstr[.]/
        continuation."""
        s = line.strip()
        if s.startswith('"') and s.endswith('"'):
            return "cont", None, po_unescape(s[1:-1])
        for kind in ("msgid_plural", "msgid", "msgstr"):
            if s.startswith(kind):
                rest = s[len(kind):].strip()
                idx = None
                if rest.startswith("["):
                    close = rest.index("]")
                    idx = int(rest[1:close])
                    rest = rest[close + 1:].strip()
                if rest.startswith('"') and rest.endswith('"'):
                    return kind, idx, po_unescape(rest[1:-1])
                return kind, idx, ""
        return None, None, None

    cur: dict | None = None
    last: tuple[str, int | None] | None = None  # append target for conts
    refs: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#:"):
            refs.append(stripped[2:].strip())
            continue
        if stripped.startswith("#"):
            continue
        kind, idx, chunk = parse_line(line)
        if kind == "msgid":
            if cur is not None:  # previous block ends here
                entries[cur["msgid"]] = cur
            cur = {"msgid": chunk, "msgid_plural": None,
                   "msgstrs": {}, "refs": refs}
            refs = []
            last = ("msgid", None)
        elif cur is None:
            continue
        elif kind == "msgid_plural":
            cur["msgid_plural"] = chunk
            last = ("plural", None)
        elif kind == "msgstr":
            key = idx if idx is not None else 0
            cur["msgstrs"][key] = chunk
            last = ("msgstr", key)
        elif kind == "cont" and last is not None:
            what, key = last
            if what == "msgid":
                cur["msgid"] += chunk
            elif what == "plural":
                cur["msgid_plural"] += chunk
            else:
                cur["msgstrs"][key] += chunk
    if cur is not None:
        entries[cur["msgid"]] = cur
    return entries


def format_entry(e: dict) -> list[str]:
    def lit(text: str) -> list[str]:
        # canonical po multi-line format: every line except the last
        # carries its trailing \n; a real newline never spans lines
        if "\n" not in text:
            return ['"%s"' % po_escape(text)]
        chunks = text.split("\n")
        out = []
        for i, c in enumerate(chunks):
            if i < len(chunks) - 1:
                out.append('"%s\\n"' % po_escape(c))
            else:
                out.append('"%s"' % po_escape(c))
        return out

    lines = []
    for ref in e.get("refs", []):
        lines.append(f"#: {ref}")
    lines.append("msgid " + lit(e["msgid"])[0])
    lines.extend(lit(e["msgid"])[1:])
    if e.get("msgid_plural"):
        lines.append("msgid_plural " + lit(e["msgid_plural"])[0])
        lines.extend(lit(e["msgid_plural"])[1:])
    keys = sorted(e["msgstrs"])
    if e.get("msgid_plural") or len(keys) > 1:
        for k in keys:
            vals = lit(e["msgstrs"][k])
            lines.append(f"msgstr[{k}] " + vals[0])
            lines.extend(vals[1:])
    else:
        vals = lit(e["msgstrs"].get(0, ""))
        lines.append("msgstr " + vals[0])
        lines.extend(vals[1:])
    return lines


def render(entries: dict[str, dict], header: str) -> str:
    out = [header.rstrip("\n")]
    for msgid in sorted(entries, key=lambda m: (m == "", m)):
        out.append("")
        out.extend(format_entry(entries[msgid]))
    return "\n".join(out) + "\n"


def lang_header(lang: str) -> str:
    """POT header + the language's Plural-Forms line."""
    extra = ""
    if lang in PLURAL_FORMS:
        extra = f'"Plural-Forms: {PLURAL_FORMS[lang]}\\n"\n' \
                f'"Language: {lang}\\n"'
    return POT_HEADER.rstrip("\n") + ("\n" + extra if extra else "")


# -- merge + report ------------------------------------------------------------

def merge(catalog: dict[str, dict], path: Path
          ) -> tuple[dict[str, dict], list[str], list[str]]:
    """Fold catalog msgids into an existing po: keeps translations,
    reports (added, dropped) msgids."""
    existing = parse_po(path)
    merged: dict[str, dict] = {}
    for msgid, e in catalog.items():
        old = existing.get(msgid)
        entry = {"msgid": msgid, "msgid_plural": e["msgid_plural"],
                 "msgstrs": dict(old["msgstrs"]) if old else {},
                 "refs": e["refs"]}
        merged[msgid] = entry
    added = [m for m in catalog if m not in existing]
    dropped = [m for m in existing if m != "" and m not in catalog]
    return merged, added, dropped

def const_msgid(node: ast.AST) -> str | None:
    """Fold a call argument into its msgid, or None when it is not a
    compile-time-constant string (f-strings are rejected loudly:
    wrapped strings must use .format() so translations can reorder)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = const_msgid(node.left), const_msgid(node.right)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        raise SystemExit(
            "f-string inside a translation call — use _(\"…{name}…\")"
            ".format(name=…) instead, so translations can move the"
            " placeholder (and the extractor sees one literal msgid)")
    return None


def catalog_files() -> list[Path]:
    """Every module the extractor walks (the whole package)."""
    return sorted((ROOT / "fluidvoice").rglob("*.py"))


def extract(files: list[Path]) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for path in sorted(files):
        tree = ast.parse(path.read_text(encoding="utf-8"),
                         filename=str(path))
        rel = path.relative_to(ROOT)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else None
            if name not in ACCESSORS:
                continue
            if not node.args:
                continue
            msgid = const_msgid(node.args[0])
            if msgid is None:
                continue
            plural = None
            if name == "_n":
                plural = const_msgid(node.args[1])
                if plural is None:
                    raise SystemExit(
                        f"_n with a non-constant plural at {rel}:{node.lineno}")
            e = entries.setdefault(
                msgid, {"msgid": msgid, "msgid_plural": plural,
                        "msgstrs": {}, "refs": []})
            if (e["msgid_plural"] or None) != (plural or None):
                raise SystemExit(f"conflicting plural for {msgid!r}")
            e["refs"].append(f"{rel}:{node.lineno}")
    return entries


# -- merge + report ------------------------------------------------------------

def completeness(entries: dict[str, dict], nplurals: int) -> list[str]:
    missing = []
    for msgid, e in entries.items():
        if msgid == "":
            continue
        need = nplurals if e.get("msgid_plural") else 1
        for i in range(need):
            if not e["msgstrs"].get(i, "").strip():
                missing.append(msgid if need == 1 else f"{msgid} [{i}]")
                break
    return missing


def main() -> int:
    check = "--check" in sys.argv
    files = catalog_files()
    catalog = extract(files)

    problems: list[str] = []

    # 1. template
    pot_entries = {m: {**e, "msgstrs": {}} for m, e in catalog.items()}
    pot_body = render(pot_entries, POT_HEADER)
    if not POT.exists() or POT.read_text(encoding="utf-8") != pot_body:
        problems.append("po/sayit-ermano.pot is stale")
        if not check:
            POT.parent.mkdir(parents=True, exist_ok=True)
            POT.write_text(pot_body, encoding="utf-8")

    # 2. per-language merge + completeness
    print(f"source strings: {len(catalog)} from {len(files)} files")
    for lang in LANGUAGES:
        if lang == "en":  # source language: no catalog
            continue
        po = PO_DIR / f"{lang}.po"
        header = lang_header(lang)
        merged, added, dropped = merge(catalog, po)
        nplurals = 3 if lang in PLURAL_FORMS else 2
        missing = completeness(merged, nplurals)
        body = render(merged, header)
        if not po.exists() or po.read_text(encoding="utf-8") != body:
            if not check:
                po.write_text(body, encoding="utf-8")
                print(f"{lang}: +{len(added)} added, -{len(dropped)} "
                      f"dropped, {len(missing)} untranslated")
            else:
                problems.append(f"po/{lang}.po is stale "
                                f"(+{len(added)} -{len(dropped)})")
        if dropped:
            problems.append(f"{lang}: {len(dropped)} orphaned msgid(s): "
                            + ", ".join(repr(m) for m in dropped[:3]))
        if missing:
            problems.append(f"{lang}: {len(missing)} untranslated "
                            f"(e.g. {missing[0]!r})")
        else:
            print(f"{lang}: complete ({len(merged)} strings)")
    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        return 1
    if not check:
        print("extraction report: all catalogs complete, zero orphans")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
