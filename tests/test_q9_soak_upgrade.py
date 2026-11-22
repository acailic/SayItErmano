"""Q9 evidence harness — the pure assertion helpers, offline with fakes.

scripts/soak_run.py and scripts/upgrade_rehearsal.py drive REAL
subprocesses (daemon, installer, venv builds); those parts live behind
the process-lane markers in tests/integration/test_q9_harness.py.
This module pins the math and the state-preservation gates with faked
sample lists and faked user state — no daemon, no installer, offline.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclass/resolution needs a module home
    spec.loader.exec_module(mod)
    return mod


soak = _load("soak_run", REPO / "scripts" / "soak_run.py")
upg = _load("upgrade_rehearsal", REPO / "scripts" / "upgrade_rehearsal.py")


def sample(t: float, rss_kb: float, socket_ok: bool = True) -> dict:
    return {"t": t, "rss_kb": rss_kb, "fds": 5, "threads": 10,
            "socket_ok": socket_ok, "socket_ms": 3.0}


# ---------------------------------------------------------------------------
# RSS verdict math
# ---------------------------------------------------------------------------

class TestRssVerdict:
    def test_flat_rss_passes(self):
        samples = [sample(0, 30_000), sample(10, 30_000),
                   sample(20, 30_000), sample(30, 30_300),
                   sample(40, 30_300)]
        v = soak.rss_verdict(samples, warmup_s=10, tolerance_pct=15.0)
        assert v["ok"] is True
        assert v["baseline_rss_kb"] == 30_000
        assert v["end_rss_kb"] == 30_300
        assert v["growth_pct"] == pytest.approx(1.0)

    def test_growth_beyond_tolerance_fails(self):
        samples = [sample(0, 30_000), sample(10, 30_000),
                   sample(20, 33_000), sample(30, 35_000), sample(40, 36_000)]
        v = soak.rss_verdict(samples, warmup_s=10, tolerance_pct=15.0)
        assert v["ok"] is False
        assert v["growth_pct"] > 15.0
        assert "tolerance" in v["reason"]

    def test_growth_exactly_at_tolerance_passes(self):
        """The gate is 'beyond' the stated number: 15.0% exactly is in."""
        samples = [sample(0, 10_000), sample(10, 10_000),
                   sample(20, 11_500), sample(30, 11_500)]
        v = soak.rss_verdict(samples, warmup_s=10, tolerance_pct=15.0)
        assert v["ok"] is True

    def test_single_sample_spike_cannot_fail_the_gate(self):
        """The end value is a median of the last window, so a one-off
        allocation blip (GC arena, a big JSON evidence write) at the
        very end must not paint the run red."""
        samples = [sample(0, 30_000), sample(10, 30_000), sample(20, 30_000),
                   sample(30, 30_000), sample(40, 90_000)]
        v = soak.rss_verdict(samples, warmup_s=10, tolerance_pct=15.0)
        assert v["ok"] is True

    def test_monotonic_small_growth_passes_but_reports_slope(self):
        samples = [sample(0, 30_000), sample(10, 30_000)] + [
            sample(10 + 10 * i, 30_000 + 10 * i) for i in range(1, 6)]
        v = soak.rss_verdict(samples, warmup_s=10, tolerance_pct=15.0)
        assert v["ok"] is True
        assert v["slope_mb_per_h"] > 0  # visible, not gated

    def test_slope_math_on_a_known_ramp(self):
        """1 kB/s linear ramp -> 3600 kB/h ~= 3.52 MB/h."""
        samples = [sample(0, 30_000), sample(10, 30_000)] + [
            sample(10 + i, 30_000 + i) for i in range(1, 11)]
        v = soak.rss_verdict(samples, warmup_s=10, tolerance_pct=15.0)
        assert v["slope_mb_per_h"] == pytest.approx(
            3600 / 1024, rel=0.05)

    def test_short_run_is_red_not_green(self):
        """A run with too few samples proved nothing: fail-safe."""
        v = soak.rss_verdict([sample(0, 30_000), sample(5, 30_100)],
                             warmup_s=10, tolerance_pct=15.0)
        assert v["ok"] is False
        assert v["reason"] == "insufficient-samples"

    def test_no_post_warmup_samples_is_red(self):
        samples = [sample(0, 30_000), sample(5, 30_000)]
        v = soak.rss_verdict(samples, warmup_s=60, tolerance_pct=15.0)
        assert v["ok"] is False and v["reason"] == "insufficient-samples"


class TestSocketVerdict:
    def test_all_answered(self):
        v = soak.socket_verdict([sample(0, 1), sample(15, 1, True)])
        assert v["ok"] is True and v["failures"] == 0

    def test_one_outage_fails_with_the_timestamp(self):
        v = soak.socket_verdict(
            [sample(0, 1), sample(15, 1, socket_ok=False), sample(30, 1)])
        assert v["ok"] is False and v["failed_at_s"] == [15]


class TestCompositeVerdict:
    def test_socket_outage_fails_even_with_flat_rss(self):
        samples = [sample(0, 30_000), sample(10, 30_000),
                   sample(20, 30_000, socket_ok=False), sample(30, 30_000)]
        v = soak.verdict(samples, warmup_s=10, tolerance_pct=15.0)
        assert v["ok"] is False
        assert any("socket" in r for r in v["reasons"])

    def test_both_gates_green(self):
        samples = [sample(0, 30_000), sample(10, 30_000),
                   sample(20, 30_000), sample(30, 30_100)]
        v = soak.verdict(samples, warmup_s=10, tolerance_pct=15.0)
        assert v["ok"] is True and v["reasons"] == []


class TestEvidenceNaming:
    def test_stem_is_date_duration(self, tmp_path):
        when = datetime(2026, 11, 22, 8, 30, tzinfo=timezone.utc)
        j, m = soak.evidence_paths(tmp_path, when, 10.0)
        assert j.name == "2026-11-22-10m.json"
        assert m.name == "2026-11-22-10m.md"

    def test_same_day_same_duration_collisions_get_a_suffix(self, tmp_path):
        when = datetime(2026, 11, 22, tzinfo=timezone.utc)
        j1, _ = soak.evidence_paths(tmp_path, when, 10.0)
        j1.write_text("{}")
        j2, _ = soak.evidence_paths(tmp_path, when, 10.0)
        assert j2.name == "2026-11-22-10m-2.json"
        assert j1 != j2

    def test_upgrade_stem_is_date(self, tmp_path):
        when = datetime(2026, 11, 22, tzinfo=timezone.utc)
        j, m = upg.evidence_paths(tmp_path, when)
        assert j.name == "2026-11-22.json" and m.name == "2026-11-22.md"


# ---------------------------------------------------------------------------
# upgrade rehearsal helpers
# ---------------------------------------------------------------------------

class TestBumpVersion:
    @pytest.mark.parametrize("before,after", [
        ("0.8.3.1", "0.8.3.2"),
        ("0.9", "0.10"),
        ("1.0.0", "1.0.1"),
        ("0.8.3.1-rc.1", "0.8.3.2-rc.1"),
    ])
    def test_bump(self, before, after):
        assert upg.bump_version(before) == after

    def test_garbage_refused(self):
        with pytest.raises(ValueError):
            upg.bump_version("not-a-version")


class TestPatchVersionInSource:
    def test_patches_the_assignment(self):
        src = '"""doc"""\n\n__version__ = "0.1.0"\n'
        assert '__version__ = "0.2.0"' in upg.patch_version_in_source(
            src, "0.2.0")

    def test_refuses_when_no_assignment(self):
        with pytest.raises(ValueError):
            upg.patch_version_in_source("no version here", "9.9.9")


class TestConfigPreserved:
    BEFORE = ('[general]\nlanguage = "en"\n'
              '\n[hotkey]\nkey = "F9"\ncancel_key = "F12"\n'
              '\n[history]\nsave = true\n')

    def test_identical_bytes(self):
        c = upg.config_preserved(self.BEFORE, self.BEFORE)
        assert c["ok"] and c["byte_identical"]
        assert c["changed"] == [] and c["missing"] == []

    def test_reformatted_but_same_keys_still_passes(self):
        """Formatting drift must not fail the keys-preserved gate."""
        after = ("# rewritten by a future installer\n"
                 '[history]\nsave = true\n[general]\nlanguage = "en"\n'
                 '[hotkey]\nkey = "F9"\ncancel_key = "F12"\n')
        c = upg.config_preserved(self.BEFORE, after)
        assert c["ok"] is True
        assert c["byte_identical"] is False

    def test_changed_value_fails(self):
        after = self.BEFORE.replace('key = "F9"', 'key = "F10"')
        c = upg.config_preserved(self.BEFORE, after)
        assert c["ok"] is False and c["changed"] == ["hotkey.key"]

    def test_dropped_key_fails(self):
        after = self.BEFORE.replace('cancel_key = "F12"\n', "")
        c = upg.config_preserved(self.BEFORE, after)
        assert c["ok"] is False and c["missing"] == ["hotkey.cancel_key"]

    def test_added_key_is_reported_not_fatal(self):
        after = self.BEFORE + '[model]\neager_warmup = false\n'
        c = upg.config_preserved(self.BEFORE, after)
        assert c["ok"] is True and c["added"] == ["model.eager_warmup"]

    def test_nested_tables_flatten_to_dotted_keys(self):
        c = upg.config_preserved(self.BEFORE, self.BEFORE)
        assert "hotkey.cancel_key" in c["keys_before"]


class TestHistoryUnchanged:
    ROWS = b'{"ts": 1, "text": "a"}\n{"ts": 2, "text": "b"}\n'

    def test_identical(self):
        h = upg.history_unchanged(self.ROWS, self.ROWS)
        assert h["ok"] and h["rows_before"] == h["rows_after"] == 2
        assert h["sha256_before"] == h["sha256_after"]

    def test_appended_row_fails(self):
        h = upg.history_unchanged(self.ROWS,
                                  self.ROWS + b'{"ts": 3, "text": "c"}\n')
        assert h["ok"] is False
        assert h["rows_before"] == 2 and h["rows_after"] == 3

    def test_rewritten_bytes_fail_even_at_same_row_count(self):
        h = upg.history_unchanged(self.ROWS,
                                  b'{"ts": 1, "text": "A"}\n'
                                  b'{"ts": 2, "text": "b"}\n')
        assert h["ok"] is False


class TestSandboxStubs:
    """The fake machine must never reach a real systemctl/apt."""

    def test_stub_log_records_calls(self, tmp_path):
        box = upg.Sandbox(tmp_path)
        r = box.run_installer()  # no args: exits 1, but stubs saw nothing real
        assert r.returncode != 0  # refused: no deb / no installation
        # nothing real ran: the only log entries, if any, are stub calls
        assert "sudo" not in box.calls()

    def test_dpkg_stub_architecture(self, tmp_path):
        box = upg.Sandbox(tmp_path)
        import subprocess
        out = subprocess.run([str(box.bin / "dpkg"), "--print-architecture"],
                             capture_output=True, text=True)
        assert out.stdout.strip() == "amd64"
