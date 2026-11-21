"""Eval-harness polish comparison + regression gate (#21): stub
polishers, hand-computed expectations, no network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fluidvoice.evalharness.manifest import Case, SynthSpec
from fluidvoice.evalharness.polish_gate import (
    RAW_VARIANT,
    PolishGate,
    run_polish_eval,
)


def case(cid: str, ref: str, tags=(), synth=True) -> Case:
    return Case(
        id=cid,
        audio=Path(f"/tmp/does-not-matter-{cid}.wav"),
        reference_text=ref,
        language="en",
        tags=tuple(tags),
        expected_guard="none",
        license="CC0-1.0",
        source="synthetic",
        synth=SynthSpec(kind="tone", seconds=0.1) if synth else None,
    )


def cleaner(text: str, c) -> dict:
    """A GOOD local polisher stand-in: removes the same fillers the raw
    corpus text carries, keeping every reference word."""
    words = [w for w in text.split()
             if w.strip().rstrip(",.!").lower() not in {"um", "uh"}]
    return {"text": " ".join(words)}


def worse(text: str, c) -> dict:
    """A polisher that DEGRADES: drops the last word (omissions)."""
    words = text.split()
    return {"text": " ".join(words[:-1])}


def erroring(text: str, c) -> dict:
    raise RuntimeError("polish backend down")


def smoke_run(tmp_path, variants, transcriber=None, gate=PolishGate()):
    cases = [
        case("c1", "Buy milk tomorrow", tags=["milk"]),
        case("c2", "Send the report today", tags=["report"]),
    ]
    return run_polish_eval(
        cases, variants, transcriber=transcriber,
        out_dir=Path(tmp_path), manifests=[Path("m.toml")], gate=gate)


def noisy_transcriber(audio, c):
    """Stand-in raw ASR output: the reference plus a filler the polish
    step is supposed to remove."""
    return {"text": c.reference_text + " um"}


class TestRunPolishEval:
    def test_smoke_report_shape_and_files(self, tmp_path):
        report = smoke_run(tmp_path, {"local": cleaner, "cloud": cleaner})
        assert (tmp_path / "polish-report.json").is_file()
        md = (tmp_path / "polish-report.md").read_text()
        assert "Regression gate" in md and "local" in md
        assert report["mode"].startswith("smoke")
        assert set(report["summary"]) == {RAW_VARIANT, "local", "cloud"}
        for row in report["cases"]:
            assert set(row["variants"]) == {RAW_VARIANT, "local", "cloud"}

    def test_good_polish_scores_zero_wer(self, tmp_path):
        report = smoke_run(tmp_path, {"local": cleaner},
                           transcriber=noisy_transcriber)
        s = report["summary"]
        # raw keeps the filler the transcriber added (WER > 0); polished
        # removes exactly it (WER 0)
        assert s[RAW_VARIANT]["wer_mean"] > 0
        assert s["local"]["wer_mean"] == 0.0

    def test_degrading_polish_worsens_wer(self, tmp_path):
        report = smoke_run(tmp_path, {"local": worse})
        assert report["summary"]["local"]["wer_mean"] \
            > report["summary"][RAW_VARIANT]["wer_mean"]
        assert report["comparison_vs_raw"]["local"]["wer_delta_vs_raw"] > 0

    def test_polisher_error_recorded_not_fatal(self, tmp_path):
        report = smoke_run(tmp_path, {"local": erroring})
        s = report["summary"]["local"]
        assert s["errors"] == 2 and s["scored"] == 0
        assert "RuntimeError" in s["error_details"]["c1"]

    def test_raw_variant_name_reserved(self, tmp_path):
        with pytest.raises(ValueError, match="built-in baseline"):
            smoke_run(tmp_path, {RAW_VARIANT: cleaner})

    def test_transcriber_used_when_given(self, tmp_path):
        report = smoke_run(tmp_path, {"local": cleaner},
                           transcriber=noisy_transcriber)
        assert report["mode"] == "evaluation"
        # the raw hypothesis (ref + filler) is what polish cleaned
        row = report["cases"][0]
        assert row["variants"][RAW_VARIANT]["text"].endswith("um")
        assert row["variants"]["local"]["text"] == row["reference_text"]


class TestGate:
    def test_local_matches_cloud_passes(self, tmp_path):
        report = smoke_run(tmp_path, {"local": cleaner, "cloud": cleaner})
        g = report["gate"]
        assert g["status"] == "passed"
        assert all(c["passed"] for c in g["checks"])
        assert g["config"]["max_wer_degradation"] == 0.02

    def test_local_worse_than_cloud_fails(self, tmp_path):
        report = smoke_run(tmp_path, {"local": worse, "cloud": cleaner})
        g = report["gate"]
        assert g["status"] == "failed"
        checks = {c["check"]: c for c in g["checks"]}
        assert checks["WER degradation vs cloud"]["passed"] is False

    def test_local_errors_fail_the_gate(self, tmp_path):
        report = smoke_run(tmp_path, {"local": erroring, "cloud": cleaner})
        g = report["gate"]
        assert g["status"] == "failed"
        checks = {c["check"]: c for c in g["checks"]}
        assert checks["local-polish errors"]["passed"] is False

    def test_recall_loss_check(self, tmp_path):
        def drops_tags(text: str, c) -> dict:  # keeps words, loses a tag?
            return {"text": text.replace("milk", "mi1k")}

        report = smoke_run(
            tmp_path, {"local": drops_tags, "cloud": cleaner},
            gate=PolishGate(max_wer_degradation=10.0, max_recall_loss=0.0))
        checks = {c["check"]: c for c in report["gate"]["checks"]}
        # word swapped -> both wer + recall degrade; only recall check
        # must be the one that fails here (wer margin is loose)
        assert checks["hotword recall loss vs cloud"]["passed"] is False
        assert report["gate"]["status"] == "failed"

    def test_margins_honored(self, tmp_path):
        # slightly-worse local within the margin passes with a loose gate
        def slightly_worse(text: str, c) -> dict:
            words = text.split()
            return {"text": " ".join(words[:-1])}
        report = smoke_run(
            tmp_path, {"local": slightly_worse, "cloud": cleaner},
            gate=PolishGate(max_wer_degradation=1.0, max_recall_loss=1.0))
        assert report["gate"]["status"] == "passed"

    def test_skipped_without_both_variants(self, tmp_path):
        report = smoke_run(tmp_path, {"local": cleaner})
        assert report["gate"]["status"] == "skipped"
        assert "cloud" in report["gate"]["reason"]

    def test_no_gate_requested(self, tmp_path):
        report = smoke_run(tmp_path, {"local": cleaner}, gate=None)
        assert report["gate"]["status"] == "skipped"


class TestCli:
    _n = 0  # unique module names: importlib caches by name across tests

    def _run(self, tmp_path, *extra, polisher_src=None):
        import sys

        from fluidvoice.evalharness.cli import main
        type(self)._n += 1
        modname = f"stub_polishers_{type(self)._n}"
        stubdir = tmp_path / "adapters"
        stubdir.mkdir()
        (stubdir / f"{modname}.py").write_text(polisher_src or (
            "def make_local():\n"
            "    return lambda text, case: {'text': ' '.join("
            "w for w in text.split() if w.lower() not in ('um', 'uh'))}\n"
            "def make_cloud():\n"
            "    return lambda text, case: {'text': ' '.join("
            "w for w in text.split() if w.lower() not in ('um', 'uh'))}\n"
        ))
        out = tmp_path / "report"
        argv = ["polish-compare", "--out", str(out),
                f"--local={modname}:make_local",
                f"--cloud={modname}:make_cloud", *extra]
        sys.path.insert(0, str(stubdir))
        try:
            return main(argv), out
        finally:
            sys.path.remove(str(stubdir))

    def test_smoke_exit_zero_when_gate_passes(self, tmp_path, capsys):
        rc, out = self._run(tmp_path)
        assert rc == 0
        data = json.loads((out / "polish-report.json").read_text())
        assert data["gate"]["status"] == "passed"
        err = capsys.readouterr().err
        assert "SMOKE mode" in err

    def test_exit_one_when_gate_fails(self, tmp_path):
        bad = ("def make_local():\n"
               "    return lambda text, case: {'text': ''}\n"
               "def make_cloud():\n"
               "    return lambda text, case: {'text': text}\n")
        rc, out = self._run(tmp_path, polisher_src=bad)
        assert rc == 1
        data = json.loads((out / "polish-report.json").read_text())
        assert data["gate"]["status"] == "failed"
