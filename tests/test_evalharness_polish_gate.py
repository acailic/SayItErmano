"""Local-vs-cloud AI polish quality gate (issue #21): corpus loading,
product-faithful scoring (errors/refusals score the raw transcript),
the regression verdict, report files, and the CLI wiring."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from fluidvoice.evalharness import main as harness_main
from fluidvoice.evalharness.polish_gate import (
    DEFAULT_CORPUS,
    DEFAULT_TOLERANCE,
    PolishCorpusError,
    load_polish_corpus,
    run_polish_gate,
)

GOOD = [{"id": "a", "raw": "um hi there", "expected": "Hi there."},
        {"id": "b", "raw": "send it now", "expected": "Send it now."}]


def _write_corpus(tmp_path: Path, cases) -> Path:
    p = tmp_path / "polish.json"
    p.write_text(json.dumps(cases), encoding="utf-8")
    return p


class TestCorpus:
    def test_packaged_corpus_loads(self):
        cases = load_polish_corpus(DEFAULT_CORPUS)
        assert len(cases) >= 10
        assert all(c.raw and c.expected for c in cases)

    def test_round_trip(self, tmp_path):
        cases = load_polish_corpus(_write_corpus(tmp_path, GOOD))
        assert [c.id for c in cases] == ["a", "b"]

    def test_bad_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{nope", encoding="utf-8")
        with pytest.raises(PolishCorpusError, match="cannot read"):
            load_polish_corpus(p)

    def test_not_a_list(self, tmp_path):
        p = _write_corpus(tmp_path, {"id": "a"})
        with pytest.raises(PolishCorpusError, match="non-empty JSON list"):
            load_polish_corpus(p)

    def test_duplicate_ids_rejected(self, tmp_path):
        p = _write_corpus(tmp_path, GOOD + [GOOD[0]])
        with pytest.raises(PolishCorpusError, match="duplicate"):
            load_polish_corpus(p)

    def test_missing_fields_rejected(self, tmp_path):
        p = _write_corpus(tmp_path, [{"id": "a", "raw": "x"}])
        with pytest.raises(PolishCorpusError, match="non-empty"):
            load_polish_corpus(p)


class TestGate:
    def run(self, tmp_path, local, cloud, tolerance=DEFAULT_TOLERANCE,
            cases=None):
        corpus = cases if cases is not None else GOOD
        return run_polish_gate(
            load_polish_corpus(_write_corpus(tmp_path, corpus)),
            local, cloud, out_dir=tmp_path / "out", tolerance=tolerance)

    def test_equal_quality_passes(self, tmp_path):
        polish = lambda t: t.upper()  # noqa: E731
        report = self.run(tmp_path, polish, polish)
        assert report["verdict"]["ok"] is True
        assert report["verdict"]["delta"] == 0.0
        assert (tmp_path / "out" / "polish-gate.json").exists()
        md = (tmp_path / "out" / "polish-gate.md").read_text()
        assert "PASS" in md and "local" in md and "cloud" in md

    def test_regression_fails(self, tmp_path):
        report = self.run(tmp_path,
                          local=lambda t: "total garbage rewrite",
                          cloud=lambda t: t)
        assert report["verdict"]["ok"] is False
        assert report["verdict"]["local_wer_mean"] > \
            report["verdict"]["cloud_wer_mean"]

    def test_tolerance_headroom(self, tmp_path):
        # local slightly worse but within the 0.5 head-room -> pass
        good = lambda t: t  # noqa: E731
        worse = lambda t: t + " x"  # noqa: E731
        report = self.run(tmp_path, worse, good, tolerance=0.5)
        assert report["verdict"]["ok"] is True
        # the same gap under a zero tolerance fails
        report0 = self.run(tmp_path, worse, good, tolerance=0.0)
        assert report0["verdict"]["ok"] is False

    def test_error_scores_the_raw_transcript(self, tmp_path):
        def local_boom(t):
            raise RuntimeError("server down")

        report = self.run(tmp_path, local_boom, lambda t: t)
        assert report["summary"]["local"]["errors"] == 2
        # raw fallback scores 0 against an equal reference? raw != expected
        # (expected carries polish), so WER > 0 - and it must be counted:
        assert report["summary"]["local"]["wer_mean"] > 0
        row = report["cases"][0]["local"]
        assert row["effective"] == GOOD[0]["raw"]
        assert "RuntimeError" in row["error"]

    def test_refusal_scores_the_raw_transcript(self, tmp_path):
        report = self.run(tmp_path,
                          lambda t: "I'm sorry, I can't assist with that.",
                          lambda t: t)
        assert report["summary"]["local"]["refusals"] == 2
        assert report["cases"][0]["local"]["effective"] == GOOD[0]["raw"]

    def test_all_erroring_side_never_passes_by_accident(self, tmp_path):
        # expected == raw for every case (an "easy" corpus): the raw-
        # transcript fallback would tie a perfect cloud polish, but an
        # always-erroring local polisher is still a hard fail
        easy = [{"id": "a", "raw": "hi there", "expected": "hi there"},
                {"id": "b", "raw": "send it", "expected": "send it"}]

        def local_boom(t):
            raise RuntimeError("x")

        report = self.run(tmp_path, local_boom, lambda t: t, cases=easy)
        assert report["summary"]["local"]["wer_mean"] == 0.0
        assert report["verdict"]["ok"] is False
        assert "errored on every case" in report["verdict"]["reason"]


class TestCLI:
    def test_polish_gate_subcommand(self, tmp_path, capsys):
        mod = tmp_path / "polish_adapter.py"
        mod.write_text(
            "def make_local():\n"
            "    return lambda t: t + '.'\n"
            "def make_cloud():\n"
            "    return lambda t: t + '.'\n", encoding="utf-8")
        sys.path.insert(0, str(tmp_path))
        try:
            out = tmp_path / "gate-out"
            rc = harness_main(
                ["polish-gate", "--out", str(out),
                 "--local", "polish_adapter:make_local",
                 "--cloud", "polish_adapter:make_cloud"])
            assert rc == 0
            report = json.loads((out / "polish-gate.json").read_text())
            assert report["verdict"]["ok"] is True
            assert "PASS" in capsys.readouterr().out
        finally:
            sys.path.remove(str(tmp_path))

    def test_regression_exit_code(self, tmp_path):
        mod = tmp_path / "polish_bad.py"
        mod.write_text(
            "def make_local():\n"
            "    return lambda t: 'junk junk junk'\n"
            "def make_cloud():\n"
            "    return lambda t: t\n", encoding="utf-8")
        import sys
        sys.path.insert(0, str(tmp_path))
        try:
            rc = harness_main(
                ["polish-gate", "--out", str(tmp_path / "g2"),
                 "--local", "polish_bad:make_local",
                 "--cloud", "polish_bad:make_cloud"])
            assert rc == 1
        finally:
            sys.path.remove(str(tmp_path))

    def test_bad_spec_exits_with_message(self, tmp_path):
        with pytest.raises(SystemExit, match="cannot load polisher"):
            harness_main(["polish-gate", "--out", str(tmp_path),
                          "--local", "nosuch:factory",
                          "--cloud", "nosuch:factory"])
