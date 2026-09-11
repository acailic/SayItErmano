"""Doctor default-source probe: classification of captured bytes."""
from __future__ import annotations

from fluidvoice.doctor import _classify_probe


def test_zero_bytes_are_silent():
    assert _classify_probe(b"\x00" * 32000) == "silent"


def test_nonzero_bytes_are_live():
    assert _classify_probe(b"\x00" * 31998 + b"\x01\x00") == "live"


def test_short_capture_is_inconclusive():
    assert _classify_probe(b"\x00" * 8000) == "inconclusive"
    assert _classify_probe(b"") == "inconclusive"
