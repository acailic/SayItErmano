"""Evaluation harness CLI, soak integration, and the `eval-run` hook.

Covers `python -m fluidvoice.evalharness` (in-process via main() plus one
subprocess smoke), the soak CSV summary, and the `sayit-ermano eval-run`
passthrough subcommand added to fluidvoice/cli.py.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from fluidvoice.evalharness import cli as eval_cli
from fluidvoice.evalharness import main as harness_main
from fluidvoice.evalharness.soak import parse_soak_rows, read_soak_csv, summarize_soak

VALID_CASE = """
[[cases]]
id = "c1"
audio = "a.wav"
reference_text = "hello world"
language = "en"
tags = ["hello"]
expected_guard = "ok"
license = "CC0-1.0"
source = "synthetic"
synth = { kind = "tone", seconds = 0.5 }
"""


@pytest.fixture()
def manifest(tmp_path: Path) -> Path:
    p = tmp_path / "manifest.toml"
    p.write_text(VALID_CASE, encoding="utf-8")
    return p


class TestSoakSummary:
    ROWS = [(0.0, 900.0, 0.0), (60.0, 905.0, 12.0), (120.0, 912.0, 24.0)]

    def test_hand_computed_summary(self):
        s = summarize_soak(self.ROWS, source="soak.csv")
        assert s["samples"] == 3
        assert s["duration_s"] == 120.0
        assert s["duration_h"] == pytest.approx(120 / 3600, abs=1e-3)
        assert s["rss_start_mb"] == 900.0
        assert s["rss_end_mb"] == 912.0
        assert s["rss_drift_mb"] == 12.0
        assert s["rss_max_mb"] == 912.0
        assert s["cpu_total_s"] == 24.0
        assert s["cpu_s_per_hour"] == pytest.approx(24 / (120 / 3600),
                                                    abs=0.1)

    def test_parse_rejects_non_numeric(self):
        with pytest.raises(ValueError, match="not numeric"):
            parse_soak_rows([["t_s", "rss_mb", "cpu_s"], ["0", "x", "1"]])

    def test_parse_skips_blank_rows_and_rejects_empty(self):
        assert parse_soak_rows([[], ["0", "900", "0"]]) == [(0.0, 900.0, 0.0)]
        with pytest.raises(ValueError, match="no data"):
            parse_soak_rows([[]])

    def test_csv_round_trip_with_header(self, tmp_path):
        csv = tmp_path / "soak.csv"
        csv.write_text("t_s,rss_mb,cpu_s\n0,900.5,0\n60,901.5,10\n",
                       encoding="utf-8")
        rows = read_soak_csv(csv)
        assert rows == [(0.0, 900.5, 0.0), (60.0, 901.5, 10.0)]
        assert summarize_soak(rows)["rss_drift_mb"] == 1.0

    def test_missing_file(self, tmp_path):
        with pytest.raises(OSError):
            read_soak_csv(tmp_path / "nope.csv")


class TestCli:
    def test_check_ok(self, manifest, capsys):
        assert harness_main(["check", "--manifest", str(manifest)]) == 0
        assert "1 cases in 1 manifest(s) valid" in capsys.readouterr().out

    def test_check_invalid_manifest(self, tmp_path, capsys):
        bad = tmp_path / "manifest.toml"
        bad.write_text("[[cases]]\nid = \"x\"\n", encoding="utf-8")
        assert harness_main(["check", "--manifest", str(bad)]) == 1
        assert "error:" in capsys.readouterr().err

    def test_check_missing_audio_without_recipe(self, tmp_path, capsys):
        body = VALID_CASE.replace('synth = { kind = "tone", seconds = 0.5 }',
                                  "")
        p = tmp_path / "manifest.toml"
        p.write_text(body, encoding="utf-8")
        assert harness_main(["check", "--manifest", str(p)]) == 1
        assert "no synth recipe" in capsys.readouterr().err

    def test_external_merge_via_cli(self, tmp_path, capsys):
        main_p = tmp_path / "manifest.toml"
        main_p.write_text(VALID_CASE, encoding="utf-8")
        ext_dir = tmp_path / "private"
        ext_dir.mkdir()
        (ext_dir / "manifest.toml").write_text(
            VALID_CASE.replace('"c1"', '"c2"'), encoding="utf-8")
        rc = harness_main(["check", "--manifest", str(main_p),
                           "--external", str(ext_dir)])
        assert rc == 0
        assert "2 cases in 2 manifest(s)" in capsys.readouterr().out

    def test_fixtures_materializes(self, manifest, capsys):
        assert harness_main(["fixtures", "--manifest", str(manifest)]) == 0
        wav = manifest.parent / "a.wav"
        assert wav.exists() and wav.stat().st_size > 44
        out = capsys.readouterr().out
        assert "c1" in out and str(wav) in out

    def test_run_with_fake_transcriber_module(self, tmp_path, manifest):
        # a tiny importable module providing the transcriber factory
        mod = tmp_path / "my_adapter.py"
        mod.write_text(
            "def make_transcriber():\n"
            "    return lambda audio_path, case: {\n"
            "        'text': 'hello world', 'guard': 'ok',\n"
            "        'first_preview_latency_s': 0.1, 'final_latency_s': 0.2,\n"
            "        'processing_time_s': 0.25}\n", encoding="utf-8")
        sys.path.insert(0, str(tmp_path))
        try:
            out = tmp_path / "report"
            rc = harness_main(["run", "--manifest", str(manifest),
                               "--out", str(out),
                               "--transcriber", "my_adapter:make_transcriber"])
            assert rc == 0
            import json
            report = json.loads((out / "report.json").read_text())
            assert report["summary"]["wer_mean"] == 0.0
            assert report["summary"]["errors"] == 0
            assert (out / "report.md").exists()
        finally:
            sys.path.remove(str(tmp_path))

    def test_run_bad_transcriber_spec(self, tmp_path, manifest, capsys):
        with pytest.raises(SystemExit, match="cannot load"):
            eval_cli._load_transcriber("nosuchmodule:factory")

    def test_run_case_error_sets_exit_code(self, tmp_path, manifest):
        mod = tmp_path / "boom_adapter.py"
        mod.write_text(
            "def make_transcriber():\n"
            "    def tr(audio_path, case):\n"
            "        raise RuntimeError('boom')\n"
            "    return tr\n", encoding="utf-8")
        sys.path.insert(0, str(tmp_path))
        try:
            rc = harness_main(["run", "--manifest", str(manifest),
                               "--out", str(tmp_path / "out"),
                               "--transcriber",
                               "boom_adapter:make_transcriber"])
            assert rc == 1
        finally:
            sys.path.remove(str(tmp_path))

    def test_run_embeds_soak_csv(self, tmp_path, manifest):
        csv = tmp_path / "soak.csv"
        csv.write_text("t_s,rss_mb,cpu_s\n0,900,0\n3600,910,30\n",
                       encoding="utf-8")
        mod = tmp_path / "my_adapter.py"
        mod.write_text(
            "def make_transcriber():\n"
            "    return lambda audio_path, case: {'text': 'hello world'}\n",
            encoding="utf-8")
        sys.path.insert(0, str(tmp_path))
        try:
            out = tmp_path / "report2"
            rc = harness_main(["run", "--manifest", str(manifest),
                               "--out", str(out), "--soak-csv", str(csv),
                               "--transcriber", "my_adapter:make_transcriber"])
            assert rc == 0
            import json
            report = json.loads((out / "report.json").read_text())
            assert report["soak"]["samples"] == 2
            assert report["soak"]["rss_drift_mb"] == 10.0
            assert "## Soak" in (out / "report.md").read_text()
        finally:
            sys.path.remove(str(tmp_path))

    def test_run_bad_soak_csv(self, tmp_path, manifest, capsys):
        csv = tmp_path / "soak.csv"
        csv.write_text("t_s,rss_mb,cpu_s\n", encoding="utf-8")
        rc = harness_main(["run", "--manifest", str(manifest),
                           "--out", str(tmp_path / "out"),
                           "--soak-csv", str(csv)])
        assert rc == 1
        assert "no data rows" in capsys.readouterr().err

    def test_soak_subcommand(self, tmp_path, capsys):
        csv = tmp_path / "soak.csv"
        csv.write_text("t_s,rss_mb,cpu_s\n0,900,0\n7200,950,60\n",
                       encoding="utf-8")
        assert harness_main(["soak", str(csv)]) == 0
        out = capsys.readouterr().out
        assert "2 samples over 2.00 h" in out
        assert "drift +50.0 MB" in out

    def test_stub_transcriber_returns_empty(self, manifest, tmp_path):
        from fluidvoice.evalharness.cli import _stub_transcriber
        from fluidvoice.evalharness.manifest import load_manifest
        case = load_manifest(manifest)[0]
        wav = tmp_path / "s.wav"
        wav.write_bytes(b"x")  # presence is all the stub needs
        assert _stub_transcriber(wav, case) == {"text": ""}

    def test_run_without_transcriber_warns_and_smokes(self, manifest, capsys):
        out = manifest.parent / "smoke-out"
        rc = harness_main(["run", "--manifest", str(manifest),
                           "--out", str(out)])
        assert rc == 0  # stub: zero case errors, WER is garbage but sane
        captured = capsys.readouterr()
        assert captured.err.count("STUB") == 1
        assert (out / "report.json").exists()

    def test_module_execution_smoke(self, manifest):
        # prove `python -m fluidvoice.evalharness` works as a process
        venv_python = Path(sys.executable)
        r = subprocess.run(
            [str(venv_python), "-m", "fluidvoice.evalharness", "check",
             "--manifest", str(manifest)],
            capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        assert "valid" in r.stdout


class TestCliHook:
    """`sayit-ermano eval-run <args>` passes through to the harness."""

    def test_hook_check(self, manifest, capsys, monkeypatch):
        from fluidvoice import cli
        monkeypatch.chdir(manifest.parent)  # keep any default paths local
        assert cli.main(["eval-run", "check", "--manifest",
                         str(manifest)]) == 0
        assert "valid" in capsys.readouterr().out

    def test_hook_run(self, manifest, capsys, monkeypatch, tmp_path):
        from fluidvoice import cli
        monkeypatch.chdir(tmp_path)
        rc = cli.main(["eval-run", "run", "--manifest", str(manifest),
                       "--out", str(tmp_path / "r")])
        assert rc == 0  # stub transcriber: runs, zero case errors
        assert (tmp_path / "r" / "report.json").exists()
        assert "STUB" in capsys.readouterr().err

    def test_hook_propagates_failure(self, tmp_path, capsys, monkeypatch):
        from fluidvoice import cli
        bad = tmp_path / "manifest.toml"
        bad.write_text("[[cases]]\nid = 'x'\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        assert cli.main(["eval-run", "check", "--manifest",
                         str(bad)]) == 1
        assert "error:" in capsys.readouterr().err
