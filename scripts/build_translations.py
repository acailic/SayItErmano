#!/usr/bin/env python3
"""Compile po/*.po into fluidvoice/locale .mo catalogs (GNU gettext).

Self-contained msgfmt (stdlib only — no gettext tools needed on the
build machine): supports plural entries and writes the standard MO
format with the hash table omitted (S = 0, like msgfmt's default for
small catalogs).

The compiled .mo files are committed: wheels/debs ship translations
without any build-time step, and this script exists to regenerate them
after po edits.

Usage:
  python scripts/build_translations.py           # compile all po/*.po
  python scripts/build_translations.py --check   # fail if a committed
                                                 # .mo is stale (CI gate)
"""
from __future__ import annotations

import array
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from extract_translations import parse_po  # noqa: E402

PO_DIR = ROOT / "po"
LOCALE_DIR = ROOT / "fluidvoice" / "locale"
DOMAIN = "sayit-ermano"
MO_MAGIC = 0x950412de


def compile_mo(entries: dict[str, dict]) -> bytes:
    """One MO file from parse_po() output (header entry included).

    Entries with an entirely-empty translation are SKIPPED (msgfmt
    behavior for untranslated rows): the runtime then misses the key
    and falls back to the English msgid instead of rendering ""."""
    items: list[tuple[bytes, bytes]] = []
    for msgid, e in entries.items():
        if msgid == "":
            forms = [e["msgstrs"].get(0, "")]
        elif e.get("msgid_plural"):
            forms = [e["msgstrs"].get(i, "") for i in range(3)]
        else:
            forms = [e["msgstrs"].get(0, "")]
        if msgid != "" and not any(f.strip() for f in forms):
            continue  # untranslated: English fallback
        mid = ("\0".join([msgid] + ([e["msgid_plural"]]
                                    if e.get("msgid_plural") else []))
               ).encode("utf-8")
        mstr = "\0".join(forms).encode("utf-8")
        items.append((mid, mstr))
    # MO string tables are sorted by msgid (binary), header first
    items.sort(key=lambda kv: kv[0])
    n = len(items)
    # absolute file offsets: header | orig table | trans table | ids | strs
    orig_off = 7 * 4
    trans_off = orig_off + 8 * n
    keystart = orig_off + 16 * n
    valuestart = keystart + sum(len(mid) + 1 for mid, _ in items)
    koffsets = array.array("I")
    voffsets = array.array("I")
    ids_blob = bytearray()
    strs_blob = bytearray()
    for mid, mstr in items:
        koffsets.extend((len(mid), keystart + len(ids_blob)))
        voffsets.extend((len(mstr), valuestart + len(strs_blob)))
        ids_blob += mid + b"\0"
        strs_blob += mstr + b"\0"
    koffsets.byteswap() if sys.byteorder == "big" else None
    voffsets.byteswap() if sys.byteorder == "big" else None
    output = struct.pack("<Iiiiiii", MO_MAGIC, 0, n, orig_off,
                         trans_off, 0, orig_off)
    output += koffsets.tobytes() + voffsets.tobytes()
    output += bytes(ids_blob) + bytes(strs_blob)
    return output


def main() -> int:
    check = "--check" in sys.argv
    problems: list[str] = []
    pos = sorted(PO_DIR.glob("*.po"))
    if not pos:
        print("no po files found", file=sys.stderr)
        return 1
    for po in pos:
        entries = parse_po(po)
        mo_bytes = compile_mo(entries)
        mo = LOCALE_DIR / po.stem / "LC_MESSAGES" / f"{DOMAIN}.mo"
        if check:
            if not mo.exists() or mo.read_bytes() != mo_bytes:
                problems.append(f"{mo.relative_to(ROOT)} is stale "
                                f"(rerun scripts/build_translations.py)")
            continue
        mo.parent.mkdir(parents=True, exist_ok=True)
        mo.write_bytes(mo_bytes)
        n = len([m for m in entries if m])
        print(f"{po.name}: {n} strings -> "
              f"{mo.relative_to(ROOT)} ({len(mo_bytes)} bytes)")
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
