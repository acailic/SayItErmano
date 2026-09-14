#!/usr/bin/env python3
"""Generated settings reference — org plan 4.3.

docs/guides/configuration.md carries a generated block: every
[section] key of the settings registry with its default, effect and
apply/secret notes. It is DERIVED from fluidvoice.config.REGISTRY —
the one registry `config init` also renders its commented template
from — so the config docs cannot drift from the code. This script
rewrites the block in place; --check exits 1 when the committed block
is stale. The unit-tier mirror is tests/test_config_reference.py
(runs inside `just gate`).

Usage:
  python scripts/gen_config_reference.py          # rewrite the block
  python scripts/gen_config_reference.py --check  # fail if stale
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fluidvoice.config import REGISTRY, SettingSpec, _toml_value  # noqa: E402

DOC = Path(__file__).resolve().parents[1] / "docs" / "guides" / "configuration.md"
BEGIN = ("<!-- BEGIN generated settings reference — "
         "regenerate: python scripts/gen_config_reference.py -->")
END = "<!-- END generated settings reference -->"

#: apply_mode (other than "immediate") -> what the user must do.
APPLY_NOTES = {
    "engine": "engine reload",
    "restart": "daemon restart",
}


def _cell(text: str) -> str:
    """One markdown table cell: collapse wrapped lines, escape pipes."""
    return " ".join(text.split()).replace("|", "\\|")


def _notes(spec: SettingSpec) -> str:
    bits = []
    if spec.apply_mode != "immediate":
        bits.append(APPLY_NOTES[spec.apply_mode])
    if spec.secret:
        bits.append("secret")
    return ", ".join(bits)


def render() -> str:
    """The generated block (without markers): one table per section,
    rows in registry (template/save) order — deterministic."""
    by_section: dict[str, list[SettingSpec]] = {}
    for spec in REGISTRY.values():
        by_section.setdefault(spec.section, []).append(spec)
    lines: list[str] = []
    for section, specs in by_section.items():
        lines.append(f"### `[{section}]`")
        lines.append("")
        lines.append("| Key | Default | Notes | Effect |")
        lines.append("| --- | --- | --- | --- |")
        for spec in specs:
            key = _cell(f"`{spec.key}`")
            default = _cell(f"`{_toml_value(spec.default)}`")
            lines.append(f"| {key} | {default} | {_notes(spec)} | "
                         f"{_cell(spec.description)} |")
        lines.append("")
    return "\n".join(lines).rstrip()


def regenerated_doc(text: str) -> str | None:
    """The doc with a fresh generated block, or None when already
    current. Raises ValueError when the markers are missing/duplicated
    (the block must be re-created by hand once)."""
    begin = text.count(BEGIN)
    end = text.count(END)
    if begin != 1 or end != 1:
        raise ValueError(
            f"expected exactly one BEGIN/END marker pair, found {begin}/{end}")
    head, _, rest = text.partition(BEGIN)
    _, _, tail = rest.partition(END)
    fresh = head + BEGIN + "\n\n" + render() + "\n" + END + tail
    return None if fresh == text else fresh


def check(doc: Path = DOC) -> list[str]:
    """Problems that make the committed doc stale (empty list = fresh)."""
    text = doc.read_text(encoding="utf-8")
    try:
        fresh = regenerated_doc(text)
    except ValueError as exc:
        return [f"{doc}: {exc}"]
    if fresh is None:
        return []
    return [f"{doc}: generated settings reference is stale — "
            "run `python scripts/gen_config_reference.py`"]


def main(argv: list[str]) -> int:
    if argv == ["--check"]:
        problems = check()
        for line in problems:
            print(line, file=sys.stderr)
        return 1 if problems else 0
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    fresh = regenerated_doc(DOC.read_text(encoding="utf-8"))
    if fresh is None:
        print(f"{DOC}: generated settings reference up to date")
    else:
        DOC.write_text(fresh, encoding="utf-8")
        print(f"{DOC}: generated settings reference rewritten")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
