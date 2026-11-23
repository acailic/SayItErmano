"""Content-free descriptors for diagnostics (excellence bet 5).

Diagnostics — daemon log lines, error text, notifications — must never
carry DICTATED CONTENT. Every log site that used to print a transcript
prefix now prints :func:`describe` output instead: a length and a short
sha256 prefix, enough to correlate lines about the same take, not
enough to reconstruct what was said.

The content itself is available only behind the explicit
``SAYITERMANO_LOG_TRANSCRIPTS=1`` opt-in, which announces itself with a
loud one-time warning per process (docs/quality/redaction-audit.md).
"""
from __future__ import annotations

import hashlib
import os
import sys

OPT_IN_ENV = "SAYITERMANO_LOG_TRANSCRIPTS"

_warned = False


def transcript_logging_enabled() -> bool:
    """True only when the user explicitly opted into content in logs."""
    return os.environ.get(OPT_IN_ENV, "") == "1"


def digest(text: str) -> str:
    """First 8 hex chars of the sha256 of ``text`` (correlation, not
    content: it cannot be reversed into speech)."""
    return hashlib.sha256(
        text.encode("utf-8", "replace")).hexdigest()[:8]


def describe(label: str, text: str, *, preview_chars: int = 0) -> str:
    """``"<label> (<N> chars, sha256:xxxxxxxx)"`` — never the content.

    With the opt-in env set and ``preview_chars`` requested, appends a
    short repr preview after printing ONE loud warning per process.
    """
    core = f"{label} ({len(text)} chars, sha256:{digest(text)})"
    if preview_chars and transcript_logging_enabled():
        _warn_once()
        return f"{core}: {text[:preview_chars]!r}"
    return core


def _warn_once() -> None:
    global _warned
    if _warned:
        return
    _warned = True
    print(f"[sayit-ermano] WARNING: {OPT_IN_ENV}=1 — dictated content WILL "
          "be written to this log. Debug only; unset it when done.",
          file=sys.stderr, flush=True)
