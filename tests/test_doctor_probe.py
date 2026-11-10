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


class TestBaselineLine:
    def test_with_and_without_baseline(self, tmp_path, monkeypatch):
        from fluidvoice import paths
        from fluidvoice.doctor import _baseline_line
        from fluidvoice.mic_quality import update_baseline
        p = tmp_path / "mic-baseline.json"
        monkeypatch.setattr(paths, "mic_baseline_file", lambda: p)
        assert "none yet" in _baseline_line("alsa_input.usb-cam")
        update_baseline("alsa_input.usb-cam", -18.0, path=p)
        line = _baseline_line("alsa_input.usb-cam")
        assert "-18.0 dBFS" in line and "too-far warning below -30.0" in line
        assert "unavailable" not in line
