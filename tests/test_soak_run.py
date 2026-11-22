"""scripts/soak_run.py — the soak gate math and /proc parsing (Q9).

Everything here is FAKE data: status text, sample lists, directory
listings. The real-subprocess soak (spawn daemon, drive takes) is the
script itself + the process-lane smoke in tests/integration/.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "soak_run", REPO / "scripts" / "soak_run.py")
soak = importlib.util.module_from_spec(_spec)
sys.modules["soak_run"] = soak
_spec.loader.exec_module(soak)


# ── /proc parsing ───────────────────────────────────────────────────────────

def test_status_parsing_reads_rss_and_threads():
    text = ("Name:\tpython3\nState:\tS (sleeping)\n"
            "VmPeak:\t  200000 kB\nVmRSS:\t  150000 kB\n"
            "Threads:\t9\n")
    assert soak.parse_status_kb(text, "VmRSS") == 150000
    assert soak.parse_status_threads(text) == 9
    assert soak.parse_status_kb(text, "VmHWM") is None


def test_status_parsing_dead_process_shape():
    assert soak.parse_status_kb("", "VmRSS") is None
    assert soak.parse_status_threads("garbage\n") is None


# ── RSS slope math ──────────────────────────────────────────────────────────

def s(t, rss, ok=True):
    return {"t_s": t, "rss_kb": rss, "socket_ok": ok, "fds": 40,
            "threads": 9}


def test_flat_rss_passes_with_zero_growth():
    samples = [s(i * 15, 100_000) for i in range(20)]
    g = soak.rss_growth(samples, warmup_s=60)
    assert g["baseline_kb"] == 100_000 and g["end_kb"] == 100_000
    assert g["growth_frac"] == 0.0
    v = soak.evaluate(samples, {"total": 10, "failed": 0}, 60, 0.15)
    assert v["passed"] and v["rss_ok"] and v["socket_ok"]


def test_warmup_spike_excluded_from_growth():
    # one-time load: 100 MB -> 200 MB INSIDE warmup, flat after
    samples = [s(0, 100_000), s(15, 120_000), s(30, 180_000), s(45, 200_000)] \
        + [s(60 + i * 15, 200_000) for i in range(16)]
    g = soak.rss_growth(samples, warmup_s=60)
    assert g["baseline_kb"] == 200_000
    assert g["growth_frac"] == 0.0


def test_slow_leak_exceeds_tolerance():
    samples = [s(i * 15, 100_000 + i * 1600) for i in range(20)]  # +19% end
    g = soak.rss_growth(samples, warmup_s=60)
    assert g["growth_frac"] > 0.15
    v = soak.evaluate(samples, {"total": 10, "failed": 0}, 60, 0.15)
    assert not v["rss_ok"] and not v["passed"]
    assert any("EXCEEDED" in r for r in v["reasons"])


def test_growth_uses_median_windows_not_single_samples():
    # one noisy end sample must not flip the verdict (median, not last)
    samples = [s(i * 15, 100_000) for i in range(20)]
    samples[-1]["rss_kb"] = 150_000
    g = soak.rss_growth(samples, warmup_s=60)
    assert g["end_kb"] == 100_000
    assert g["growth_frac"] == 0.0


def test_too_few_samples_is_inconclusive_and_fails_closed():
    assert soak.rss_growth([], warmup_s=60) is None
    assert soak.rss_growth([s(0, 1), s(10, 1)], warmup_s=60) is None
    v = soak.evaluate([s(0, 100)], {"total": 0, "failed": 0}, 60, 0.15)
    assert not v["rss_ok"]
    assert any("insufficient" in r for r in v["reasons"])


# ── socket-health gate ──────────────────────────────────────────────────────

def test_socket_failure_fails_even_with_flat_rss():
    samples = [s(i * 15, 100_000) for i in range(10)]
    samples[7]["socket_ok"] = False
    v = soak.evaluate(samples, {"total": 5, "failed": 0}, 60, 0.15)
    assert v["rss_ok"] and not v["socket_ok"] and not v["passed"]
    assert any("socket failed" in r for r in v["reasons"])


def test_take_failures_surface_in_reasons_field():
    samples = [s(i * 15, 100_000) for i in range(10)]
    v = soak.evaluate(samples, {"total": 5, "failed": 2}, 60, 0.15)
    assert v["passed"]  # takes failing is data, the gates decide
    assert v["takes_failed"] == 2


# ── evidence rendering ──────────────────────────────────────────────────────

def test_markdown_summary_carries_the_math():
    md = soak.markdown_summary({"runs": [
        {"date": "2026-11-22", "duration_min": 10, "takes_total": 38,
         "growth": {"baseline_kb": 92718, "growth_frac": 0.0022},
         "rss_tolerance": 0.15, "passed": True,
         "file": "2026-11-22-10m.json"},
        {"date": "2026-11-21", "duration_min": 2, "takes_total": 4,
         "growth": None, "rss_tolerance": 0.15, "passed": False,
         "file": "2026-11-21-2m.json"},
    ]})
    assert "+0.2%" in md and "+15%" in md and "PASS" in md and "FAIL" in md
    assert "2026-11-22-10m.json" in md


def test_write_evidence_round_trip(tmp_path):
    report = {"started_utc": "2026-11-22T05:00:00Z", "duration_min": 10,
              "verdict": {"takes_total": 3, "growth": {"baseline_kb": 1,
                                                       "growth_frac": 0.0},
                          "rss_tolerance": 0.15, "passed": True}}
    path = soak.write_evidence(report, evidence_dir=tmp_path)
    assert path.name == "2026-11-22-10m.json"
    assert "2026-11-22-10m.json" in (tmp_path / "README.md").read_text()


def test_write_evidence_skips_malformed_json(tmp_path):
    (tmp_path / "garbage.json").write_text("{not json")
    soak.write_evidence({"started_utc": "2026-11-22T05:00:00Z",
                         "duration_min": 1,
                         "verdict": {"takes_total": 0, "growth": None,
                                     "rss_tolerance": 0.15,
                                     "passed": True}},
                        evidence_dir=tmp_path)
    assert "garbage" not in (tmp_path / "README.md").read_text()


# ── daemon config/shim plumbing (no subprocess) ────────────────────────────

def test_daemon_config_is_the_model_free_shape(tmp_path):
    d = soak.SoakDaemon(tmp_path, tmp_path / "daemon.log")
    d2 = soak.SoakDaemon(tmp_path, tmp_path / "daemon.log")  # idempotent dirs
    env = d2.env
    assert env["SAYITERMANO_NO_APP_SPAWN"] == "1"
    assert env["SAYITERMANO_SKIP_UPDATE_CHECK"] == "1"
    assert env["HF_HUB_OFFLINE"] == "1"  # model-free take cycle
    assert "WAYLAND_DISPLAY" not in env
    assert str(d.socket) == env["SAYITERMANO_SOCKET"]
    cfg = (tmp_path / "config.toml").read_text()
    assert 'key = "F9"' in cfg and "eager_warmup = false" in cfg
    shim = (tmp_path / "bin" / "pw-record").read_text()
    assert "signal.SIGINT" in shim and "sys.argv[-1]" in shim
