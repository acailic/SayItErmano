"""Perf-baseline harness tests (quality plan Q8, perf half).

Same discipline as the rest of the eval harness: the MATH and FILE
logic — aggregation, the tolerance comparison, baseline validation,
newest-first resolution, malformed rejection, the whisper.cpp timing
parser — is pinned here with hand-computed cases and a FAKE backend.
No model load, no network, no binary: real backends only produce the
timings being recorded (fluidvoice/evalharness/perf_backends.py).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

from fluidvoice.evalharness import perf
from fluidvoice.evalharness.manifest import load_manifest
from fluidvoice.evalharness.perf import (
    GATED_METRICS,
    PerfBaselineError,
    Take,
    aggregate,
    compare,
    load_baseline,
    measure_corpus,
    newest_baseline,
    render_markdown_summary,
    validate_baseline,
    write_baseline,
)

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------- aggregation

def test_aggregate_means_and_throughput():
    report = aggregate(
        [Take("a", audio_duration_s=1.0, wall_time_s=0.5),
         Take("b", audio_duration_s=2.0, wall_time_s=1.0)],
        model_load_s=2.0, peak_rss_mb=123.0,
        backend={"name": "fake"}, host="test-2cpu")
    m = report["metrics"]
    # mean wall: (0.5 + 1.0) / 2
    assert m["wall_time_mean_s"] == pytest.approx(0.75)
    # throughput: total audio / total wall = 3.0 / 1.5
    assert m["audio_seconds_per_second"] == pytest.approx(2.0)
    assert m["model_load_s"] == 2.0
    assert m["peak_rss_mb"] == 123.0
    assert report["host_class"] == "test-2cpu"
    assert report["schema"] == perf.SCHEMA
    # takes land verbatim — a report can be re-analyzed without a rerun
    assert [t["case_id"] for t in report["takes"]] == ["a", "b"]
    assert report["totals"] == {"cases": 2, "audio_s": 3.0, "wall_s": 1.5}


def test_aggregate_rejects_empty_take_set():
    with pytest.raises(PerfBaselineError, match="empty take set"):
        aggregate([], model_load_s=1.0, peak_rss_mb=1.0,
                  backend={}, host="h")


def test_every_gated_metric_is_aggregated():
    """The gate can only compare what the recorder records."""
    m = aggregate([Take("a", 1.0, 1.0)], model_load_s=1.0,
                  peak_rss_mb=1.0, backend={}, host="h")["metrics"]
    for name in GATED_METRICS:
        assert isinstance(m[name], float), name


# ------------------------------------------------------ measure_corpus

class FakeBackend:
    """The daemon-shape seam with no model: deterministic, offline."""

    name = "fake"

    def __init__(self):
        self.model_load_s = 4.0          # adapter-measured load
        self.load_calls = 0
        self.takes: list[str] = []

    def load(self):
        self.load_calls += 1

    def transcribe(self, wav, case):
        self.takes.append(case.id)

    def peak_rss_mb(self):
        return 64.0

    def describe(self):
        return {"name": self.name, "model": "none",
                "process_shape": "in-process fake (tests)"}


CASE_TOML = """
[[cases]]
id = "sil"
audio = "sil.wav"
reference_text = ""
language = "en"
tags = []
expected_guard = "flag"
license = "CC0-1.0"
source = "synthetic (test)"
synth = { kind = "silence", seconds = 0.2 }
"""


def test_measure_corpus_plumbing(tmp_path):
    """One load, one timed take per case, adapter facts recorded."""
    (tmp_path / "manifest.toml").write_text(CASE_TOML, encoding="utf-8")
    cases = load_manifest(tmp_path / "manifest.toml")
    backend = FakeBackend()

    report = measure_corpus(cases, backend, host="test-1cpu",
                            manifests=[tmp_path / "manifest.toml"])

    assert backend.load_calls == 1
    assert backend.takes == ["sil"]
    m = report["metrics"]
    # the fake's own model_load_s wins over the (near-zero) timed load
    assert m["model_load_s"] == 4.0
    assert m["peak_rss_mb"] == 64.0
    assert m["wall_time_mean_s"] >= 0.0
    assert report["takes"][0]["case_id"] == "sil"
    # synth materialized the audio and its duration was measured
    assert report["takes"][0]["audio_duration_s"] == pytest.approx(0.2,
                                                                  abs=0.05)
    assert report["backend"]["name"] == "fake"
    assert report["corpus"]["cases"] == 1
    assert report["host_class"] == "test-1cpu"


def test_measure_corpus_falls_back_to_timed_load(tmp_path):
    (tmp_path / "manifest.toml").write_text(CASE_TOML, encoding="utf-8")
    cases = load_manifest(tmp_path / "manifest.toml")
    backend = FakeBackend()
    backend.model_load_s = None       # shape with a real one-time load
    t0 = time.perf_counter()

    report = measure_corpus(cases, backend, host="h")

    assert report["metrics"]["model_load_s"] == pytest.approx(
        time.perf_counter() - t0, abs=5.0)


# ------------------------------------------------------------- the gate

def _report(**metric_overrides):
    report = aggregate(
        [Take("a", 1.0, 0.5), Take("b", 2.0, 1.0)],
        model_load_s=2.0, peak_rss_mb=100.0,
        backend={"name": "fake"}, host="test-1cpu")
    report["metrics"].update(metric_overrides)
    return report


def _by_metric(comparisons):
    return {c.metric: c for c in comparisons}


class TestCompare:
    def test_identical_runs_pass(self):
        base = _report()
        assert not any(c.regressed for c in compare(_report(), base))

    def test_lower_metric_beyond_tolerance_fails(self):
        base = _report(wall_time_mean_s=1.0)
        got = _by_metric(compare(_report(wall_time_mean_s=1.25), base))
        assert not got["wall_time_mean_s"].regressed
        got = _by_metric(compare(_report(wall_time_mean_s=1.3), base))
        assert got["wall_time_mean_s"].regressed

    def test_higher_metric_beyond_tolerance_fails(self):
        base = _report(audio_seconds_per_second=2.0)
        got = _by_metric(compare(
            _report(audio_seconds_per_second=1.5), base))
        assert not got["audio_seconds_per_second"].regressed
        got = _by_metric(compare(
            _report(audio_seconds_per_second=1.4), base))
        assert got["audio_seconds_per_second"].regressed

    def test_improvements_never_fail(self):
        base = _report()
        fresh = _report(wall_time_mean_s=0.01, model_load_s=0.01,
                        peak_rss_mb=1.0,
                        audio_seconds_per_second=99.0)
        assert not any(c.regressed for c in compare(fresh, base))

    def test_exactly_at_tolerance_passes(self):
        """'> 25 % regresses': the boundary itself is clean."""
        base = _report(model_load_s=1.0, wall_time_mean_s=1.0,
                       peak_rss_mb=100.0, audio_seconds_per_second=2.0)
        fresh = _report(model_load_s=1.25, wall_time_mean_s=1.25,
                        peak_rss_mb=125.0,
                        audio_seconds_per_second=2.0 / 1.25)
        assert not any(c.regressed for c in compare(fresh, base))

    def test_custom_tolerance(self):
        base = _report(model_load_s=1.0)
        fresh = _report(model_load_s=1.1)
        got = _by_metric(compare(fresh, base, tolerance=0.05))
        assert got["model_load_s"].regressed
        got = _by_metric(compare(fresh, base, tolerance=0.5))
        assert not got["model_load_s"].regressed

    def test_zero_baseline_rules(self):
        zero = _report(peak_rss_mb=0.0)
        # lower-better from zero: ANY nonzero value is a regression
        got = _by_metric(compare(_report(peak_rss_mb=0.001), zero))
        assert got["peak_rss_mb"].regressed
        got = _by_metric(compare(_report(peak_rss_mb=0.0), zero))
        assert not got["peak_rss_mb"].regressed
        # higher-better from zero: nothing can regress below zero
        zero_t = _report(audio_seconds_per_second=0.0)
        got = _by_metric(compare(
            _report(audio_seconds_per_second=0.0), zero_t))
        assert not got["audio_seconds_per_second"].regressed

    def test_delta_pct_direction_aware(self):
        base = _report(wall_time_mean_s=1.0,
                       audio_seconds_per_second=2.0)
        got = _by_metric(compare(
            _report(wall_time_mean_s=1.3,
                    audio_seconds_per_second=1.0), base))
        assert got["wall_time_mean_s"].delta_pct == \
            pytest.approx(0.3)      # 30 % slower
        assert got["audio_seconds_per_second"].delta_pct == \
            pytest.approx(0.5)      # throughput halved = 50 % worse

    def test_invalid_tolerance_rejected(self):
        with pytest.raises(PerfBaselineError, match="tolerance"):
            compare(_report(), _report(), tolerance=-0.1)

    def test_all_gated_metrics_compared(self):
        assert [c.metric for c in compare(_report(), _report())] == \
            list(GATED_METRICS)


# -------------------------------------------- validation / resolution

def _valid_body() -> dict:
    return _report()


class TestValidateBaseline:
    def test_valid_passes(self):
        assert validate_baseline(_valid_body()) == _valid_body()

    @pytest.mark.parametrize("mangle", [
        lambda d: d.update(schema="perf-baseline/0"),
        lambda d: d.pop("schema"),
        lambda d: d.pop("host_class"),
        lambda d: d.pop("metrics"),
        lambda d: d.pop("generated_utc"),
        lambda d: d.update(host_class=""),
        lambda d: d["metrics"].pop("model_load_s"),
        lambda d: d["metrics"].update(model_load_s="fast"),
        lambda d: d["metrics"].update(peak_rss_mb=True),
        lambda d: d["metrics"].update(wall_time_mean_s=-1.0),
    ])
    def test_malformed_rejected(self, mangle):
        d = _valid_body()
        mangle(d)
        with pytest.raises(PerfBaselineError):
            validate_baseline(d, source="mangled.json")

    def test_not_a_dict_rejected(self):
        with pytest.raises(PerfBaselineError):
            validate_baseline([1, 2, 3])

    def test_nan_rejected(self):
        d = _valid_body()
        d["metrics"]["model_load_s"] = float("nan")
        with pytest.raises(PerfBaselineError, match="finite"):
            validate_baseline(d)


def _write_baseline(tmp_path: Path, name: str, body) -> Path:
    p = tmp_path / name
    p.write_text(body if isinstance(body, str) else json.dumps(body),
                 encoding="utf-8")
    return p


class TestNewestBaseline:
    def test_newest_date_wins(self, tmp_path):
        _write_baseline(tmp_path, "2026-01-05-old.json", _report())
        _write_baseline(tmp_path, "2026-01-20-new.json", _report())
        assert newest_baseline(tmp_path) == tmp_path / "2026-01-20-new.json"

    def test_same_day_mtime_wins(self, tmp_path):
        a = _write_baseline(tmp_path, "2026-03-01-am.json", _report())
        b = _write_baseline(tmp_path, "2026-03-01-pm.json", _report())
        old = time.time() - 3600
        os.utime(a, (old, old))
        assert newest_baseline(tmp_path) == b

    def test_empty_directory_is_none(self, tmp_path):
        assert newest_baseline(tmp_path) is None

    def test_malformed_newest_raises_not_skips(self, tmp_path):
        """Corrupted newest evidence fails loudly — never quietly
        compares against an older file."""
        _write_baseline(tmp_path, "2026-01-05-good.json", _report())
        _write_baseline(tmp_path, "2026-02-01-bad.json", "{not json")
        with pytest.raises(PerfBaselineError, match="bad.json"):
            newest_baseline(tmp_path)

    def test_malformed_older_than_a_valid_newest_is_untouched(
            self, tmp_path):
        _write_baseline(tmp_path, "2026-01-05-bad.json", "[]")
        _write_baseline(tmp_path, "2026-02-01-good.json", _report())
        assert newest_baseline(tmp_path) == \
            tmp_path / "2026-02-01-good.json"

    def test_non_json_files_ignored(self, tmp_path):
        _write_baseline(tmp_path, "README.md", "# summary")
        assert newest_baseline(tmp_path) is None


def test_load_baseline_error_names_the_file(tmp_path):
    p = _write_baseline(tmp_path, "2026-01-01-x.json", '{"schema": 1}')
    with pytest.raises(PerfBaselineError, match="2026-01-01-x.json"):
        load_baseline(p)


def test_load_baseline_missing_file(tmp_path):
    with pytest.raises(PerfBaselineError, match="cannot read"):
        load_baseline(tmp_path / "nope.json")


# -------------------------------------------------------- file writing

def test_write_baseline_names_file_from_stamps(tmp_path):
    report = _report()
    path = write_baseline(report, tmp_path)
    assert path.name.endswith("-test-1cpu.json")
    assert path.name.startswith(report["generated_utc"][:10])
    assert load_baseline(path)["host_class"] == "test-1cpu"


def test_write_baseline_same_day_rerecord_overwrites(tmp_path):
    first = write_baseline(_report(), tmp_path)
    second = write_baseline(_report(), tmp_path)
    assert first == second
    assert list(tmp_path.glob("*.json")) == [first]


def test_regenerate_summary_table(tmp_path):
    older = _report()
    older["generated_utc"] = "2026-01-01T00:00:00+00:00"
    newer = _report()
    newer["generated_utc"] = "2026-02-02T00:00:00+00:00"
    write_baseline(older, tmp_path)
    write_baseline(newer, tmp_path)

    assert perf.regenerate_summary(tmp_path)
    table = (tmp_path / "README.md").read_text(encoding="utf-8")
    # newest first, one row per baseline, links are relative
    assert table.index("2026-02-02") < table.index("2026-01-01")
    assert "](2026-02-02-test-1cpu.json)" in table

    assert not perf.regenerate_summary(tmp_path / "empty")


def test_render_markdown_summary_is_full_width():
    body = render_markdown_summary([_report(), _report()])
    assert body.startswith("# Perf baselines")
    assert "audio s/s" in body and "peak RSS" in body


# ------------------------------------------- whisper.cpp timing parser

from fluidvoice.evalharness.perf_backends import parse_whispercpp_load_ms  # noqa: E402


class TestWhisperCppParse:
    STDERR = """
whisper_backend_init_gpu: no GPU found
whisper_print_timings:     load time =    53.52 ms
whisper_print_timings:     mel time =     3.45 ms
whisper_print_timings:   sample time =    1.20 ms
"""

    def test_parses_load_ms(self):
        assert parse_whispercpp_load_ms(self.STDERR) == \
            pytest.approx(53.52)

    def test_absent_returns_none(self):
        assert parse_whispercpp_load_ms("no timings here") is None


# --------------------------------------------------- gate CLI contract

def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_perf_gate_no_baseline_exits_2(tmp_path, capsys):
    gate = _load_script("perf_gate")
    rc = gate.main(["--dir", str(tmp_path)])
    assert rc == 2
    assert "no recorded baseline" in capsys.readouterr().err


def test_perf_baseline_help_contract():
    """The record CLI stays runnable offline: --help parses."""
    base = _load_script("perf_baseline")
    with pytest.raises(SystemExit) as e:
        base.main(["--help"])
    assert e.value.code == 0
