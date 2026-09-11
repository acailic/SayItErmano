"""Minimal TOML serializer for the corpus tooling's output shapes.

The project deliberately avoids a TOML *writer* dependency (only
``tomllib`` is stdlib); the sealed split file and the exported harness
manifest use a small, well-understood value domain:

=========================  ==========================================
type                       rendering
=========================  ==========================================
``str``                    basic string with full escaping
``bool`` / ``int``         literal
``float``                  ``repr`` (finite values only)
``list`` of scalars        inline, or multi-line when long
``dict`` (top level)       key = value lines; ``[[table]]`` via
                           :func:`dump_cases` helpers in callers
=========================  ==========================================

Everything else raises ``TypeError`` loudly — this is a writer for our
own artifacts, not a general-purpose TOML library.
"""
from __future__ import annotations

import math
from typing import Any

_ESCAPE = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_LONG_LIST = 6  # lists longer than this render one value per line


def _escape_string(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch in _ESCAPE:
            out.append(_ESCAPE[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _fmt_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise TypeError(f"non-finite float is not TOML-able: {v!r}")
        return repr(v)
    if isinstance(v, str):
        return _escape_string(v)
    raise TypeError(f"unsupported TOML scalar: {type(v).__name__} ({v!r})")


def _fmt_list(values: list[Any]) -> str:
    items = [_fmt_scalar(v) for v in values]
    if len(items) <= _LONG_LIST:
        return "[" + ", ".join(items) + "]"
    inner = ",\n".join("  " + i for i in items)
    return "[\n" + inner + ",\n]"


def _fmt_key(k: str) -> str:
    if k and all(c.isalnum() or c in "_-" for c in k):
        return k
    return _escape_string(k)


def dump_toml(data: dict[str, Any]) -> str:
    """Render a flat (str-keyed, scalar/flat-list valued) dict as TOML."""
    lines: list[str] = []
    for k, v in data.items():
        if isinstance(v, list):
            lines.append(f"{_fmt_key(k)} = {_fmt_list(v)}")
        else:
            lines.append(f"{_fmt_key(k)} = {_fmt_scalar(v)}")
    lines.append("")  # trailing newline
    return "\n".join(lines)


def dump_case_table(case: dict[str, Any]) -> str:
    """Render one ``[[cases]]`` table (harness manifest row)."""
    lines = ["[[cases]]"]
    for k, v in case.items():
        if isinstance(v, list):
            lines.append(f"{_fmt_key(k)} = {_fmt_list(v)}")
        else:
            lines.append(f"{_fmt_key(k)} = {_fmt_scalar(v)}")
    return "\n".join(lines) + "\n"
