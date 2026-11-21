"""Polish-quality bench (issue #21 gate): offline scoring math, corpus
loading, gate verdicts, report rendering, and the CLI subcommand — no
models, no network (the local provider is covered by the stub-server
suite in tests/test_local_ai.py)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fluidvoice.evalharness import polish as P
from fluidvoice.evalharness.cli import main as eval_main

# -- corpus ---------------------------------------------------------------


class TestCorpus:
    def test_packaged_corpus_loads(self):
        cases = P.load_polish_corpus()
        assert len(cases) >= 12, "a gate needs a real corpus"
        assert all(c.raw and c.expected for c in cases)
        assert len({c.id for c in cases}) == len(cases)

    def test_corpus_references_are_polished(self):
        """Every expected text starts a sentence with a capital and ends
        with terminal punctuation; no raw filler survives (the baseline
        must be a real polish target, not a copy of the raw text)."""
        from fluidvoice.config import DEFAULT_FILLERS
        for c in P.load_polish_corpus():
            assert c.expected[0].isupper(), c.id
            assert c.expected[-1] in ".!?", c.id
            words = {w.strip(".,?!'").lower() for w in c.expected.split()}
            assert not (words & set(DEFAULT_FILLERS)), c.id

    def test_load_errors_are_loud(self, tmp_path):
        empty = tmp_path / "empty.toml"
        empty.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="no cases"):
            P.load_polish_corpus(empty)
        bad = tmp_path / "bad.toml"
        bad.write_text('[[case]]\nid = "x"\nraw = "a"\n', encoding="utf-8")
        with pytest.raises(ValueError, match="non-empty"):
            P.load_polish_corpus(bad)
        dup = tmp_path / "dup.toml"
        dup.write_text(
            '[[case]]\nid = "x"\nraw = "a"\nexpected = "b."\n'
            '[[case]]\nid = "x"\nraw = "c"\nexpected = "d."\n',
            encoding="utf-8")
        with pytest.raises(ValueError, match="duplicate"):
            P.load_polish_corpus(dup)


# -- metrics ----------------------------------------------------------------


class TestMetrics:
    def test_perfect_output(self):
        m = P.polish_case_metrics("Let's meet on Tuesday at 4 p.m.",
                                  "Let's meet on Tuesday at 4 p.m.")
        assert m["exact"] and m["similarity"] == 1.0
        assert m["content_recall"] == 1.0
        assert not m["empty"] and not m["refusal"]

    def test_case_insensitive_punctuation_free(self):
        m = P.polish_case_metrics("meet on tuesday",
                                  "Meet on Tuesday!")
        assert m["exact"], "normalization ignores case + punctuation"
        assert m["similarity"] == 1.0

    def test_dropped_word_costs_recall_not_synonyms(self):
        m = P.polish_case_metrics("Meet on Tuesday.",  # "not" dropped
                                  "Do not meet on Tuesday.")
        assert m["content_recall"] < 1.0
        m2 = P.polish_case_metrics("Meet on Tuesday please.",
                                   "Do not meet on Tuesday.")
        assert m2["content_recall"] < 1.0

    def test_paraphrase_keeps_some_recall_lowers_similarity(self):
        m = P.polish_case_metrics("We could meet on Tuesday afternoon.",
                                  "Let's meet on Tuesday at 4 p.m.")
        assert 0 < m["content_recall"] < 1.0, "key words survive, not all"
        assert m["similarity"] < 0.9, "wording drifts"

    def test_empty_and_refusal_are_hard_failures(self):
        empty = P.polish_case_metrics("", "Polished.")
        assert empty["empty"] and empty["similarity"] == 0.0
        refusal = P.polish_case_metrics(
            "I'm sorry, but I can't help with that.", "Polished.")
        assert refusal["refusal"]
        assert refusal["content_recall"] == 0.0

    def test_similarity_clamped_at_zero(self):
        m = P.polish_case_metrics("x" * 100, "A.")
        assert m["similarity"] == 0.0, "CER > 1 clamps, never negative"


# -- gate ---------------------------------------------------------------------


def _prov(similarity: float, recall: float, hard: int = 0) -> dict:
    return {"summary": {"similarity_mean": similarity,
                        "content_recall_mean": recall,
                        "hard_failures": hard}}


class TestGate:
    def test_pass_within_margin(self):
        g = P.evaluate_gate(_prov(0.90, 0.95), _prov(0.93, 0.99), margin=0.05)
        assert g["verdict"] == "PASS"

    def test_fail_on_similarity_regression(self):
        g = P.evaluate_gate(_prov(0.70, 0.95), _prov(0.93, 0.99), margin=0.05)
        assert g["verdict"] == "FAIL"
        assert [c["metric"] for c in g["checks"]] == \
            ["content_recall_mean", "similarity_mean"]
        assert g["checks"][1]["within_margin"] is False

    def test_fail_on_recall_regression(self):
        g = P.evaluate_gate(_prov(0.95, 0.80), _prov(0.93, 0.99), margin=0.05)
        assert g["verdict"] == "FAIL"

    def test_local_may_exceed_cloud(self):
        g = P.evaluate_gate(_prov(0.99, 1.0), _prov(0.90, 0.95), margin=0.05)
        assert g["verdict"] == "PASS"

    def test_hard_failures_fail_even_in_margin(self):
        g = P.evaluate_gate(_prov(0.95, 0.99, hard=1), _prov(0.93, 0.95))
        assert g["verdict"] == "FAIL"
        assert g["checks"][-1]["metric"] == "hard_failures"


# -- bench + report ----------------------------------------------------------------


class TestBench:
    def run_bench(self, tmp_path, outputs: dict[str, str],
                  gate=True, margin=0.05) -> dict:
        cases = [
            P.PolishCase("a", "um a", "A."),
            P.PolishCase("b", "um b", "B."),
        ]
        providers = {P.NO_POLISH: P.identity_polisher}
        if "local" in outputs:
            providers[P.LOCAL] = (lambda raw, o=outputs["local"]: o)
        if "cloud" in outputs:
            providers[P.CLOUD] = (lambda raw, o=outputs["cloud"]: o)
        return P.run_polish_bench(
            cases, providers, out_dir=tmp_path / "out",
            gate=("local", "cloud") if gate else None,
            margin=margin, provenance={"manifest": "test"})

    def test_bench_writes_reports_and_gate(self, tmp_path):
        report = self.run_bench(tmp_path, {"local": "A.", "cloud": "A."})
        out = tmp_path / "out"
        md = (out / "polish-report.md").read_text(encoding="utf-8")
        data = json.loads((out / "polish-report.json").read_text())
        assert data["kind"] == "polish-bench"
        assert report["gate"]["verdict"] == "PASS"
        assert "**PASS**" in md and "| a | local" in md
        names = {r["name"] for r in report["providers"]}
        assert names == {P.NO_POLISH, P.LOCAL, P.CLOUD}

    def test_baseline_row_scores_raw(self, tmp_path):
        report = self.run_bench(tmp_path, {}, gate=False)
        base = next(r for r in report["providers"]
                    if r["name"] == P.NO_POLISH)
        assert base["baseline_raw"] is True
        assert base["summary"]["exact_matches"] == 0  # raw != polished

    def test_provider_errors_are_recorded_not_raised(self, tmp_path):
        def boom(raw):
            raise RuntimeError("provider down")
        cases = [P.PolishCase("a", "um a", "A.")]
        report = P.run_polish_bench(
            cases, {"exploder": boom, P.NO_POLISH: P.identity_polisher},
            out_dir=tmp_path / "o2", gate=None)
        r = next(r for r in report["providers"] if r["name"] == "exploder")
        assert r["summary"]["errors"] == 1
        assert r["summary"]["hard_failures"] == 1
        assert "provider down" in r["per_case"][0]["error"]

    def test_gate_skipped_reports_why(self, tmp_path):
        report = self.run_bench(tmp_path, {"local": "A."}, gate=True)
        assert report["gate"]["verdict"] == "SKIPPED"
        assert "cloud" in report["gate"]["reason"]

    def test_markdown_escapes_and_truncates(self, tmp_path):
        cases = [P.PolishCase("a", "um a", "A.")]
        long = "| pipe " + "word " * 50
        P.run_polish_bench(
            cases, {"chatty": lambda raw: long}, out_dir=tmp_path / "o3",
            gate=None)
        md = (tmp_path / "o3" / "polish-report.md").read_text(encoding="utf-8")
        assert "\\|" in md
        assert "..." in md


# -- providers (offline) -------------------------------------------------------


class TestProviders:
    def test_local_polisher_closes_its_server(self, tmp_path, monkeypatch):
        """Regression: a bench run must never leak the llama-server child
        (the first real-model run left one on the default port)."""
        import threading

        from fluidvoice import model_catalog
        from fluidvoice.ai import local as L
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        p = model_catalog.chat_model_path(model_catalog.CHAT_DEFAULT_MODEL)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"gguf")
        port = 0
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        cfg = {"ai": {"enabled": True, "local_polish": True,
                      "local_model": model_catalog.CHAT_DEFAULT_MODEL,
                      "local_port": port}}
        stub = tmp_path / "stub.py"
        stub.write_text(
            "import sys\n"
            "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
            "port = int(sys.argv[sys.argv.index('--port') + 1])\n"
            "class H(BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        self.send_response(200); self.end_headers()\n"
            "    def log_message(self, *a): pass\n"
            "HTTPServer(('127.0.0.1', port), H).serve_forever()\n",
            encoding="utf-8")
        started = threading.Event()
        real_init = L.LocalServer.__init__

        def patched_init(self, *a, **kw):
            kw["command"] = [__import__("sys").executable, "-u", str(stub)]
            real_init(self, *a, **kw)
            started.set()

        monkeypatch.setattr(L.LocalServer, "__init__", patched_init)
        polisher = P.make_local_polisher(cfg)
        assert started.wait(5)
        cases = [P.PolishCase("a", "um a", "A.")]
        P.run_polish_bench(cases, {"local": polisher},
                           out_dir=tmp_path / "out", gate=None)
        # close hook fired with the bench
        import time as _t
        deadline = _t.monotonic() + 5
        while L.probe_health(port) and _t.monotonic() < deadline:
            _t.sleep(0.05)
        assert not L.probe_health(port), "server must stop after the bench"

    def test_endpoint_polisher_config_errors(self):
        with pytest.raises(ValueError, match="base URL"):
            P.make_endpoint_polisher("", "")

    def test_local_polisher_fails_fast_without_model(self, tmp_path,
                                                     monkeypatch):
        from fluidvoice import model_catalog
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        with pytest.raises(Exception, match="not downloaded"):
            P.make_local_polisher(
                {"ai": {"enabled": True, "local_polish": True,
                        "local_model": model_catalog.CHAT_DEFAULT_MODEL}})


# -- CLI -------------------------------------------------------------------------


class TestCLI:
    def test_smoke_baseline_only(self, tmp_path, capsys):
        rc = eval_main(["polish", "--out", str(tmp_path / "r")])
        assert rc == 0
        out = capsys.readouterr().out
        assert "no-polish (baseline)" in out
        assert (tmp_path / "r" / "polish-report.md").exists()

    def test_require_gate_without_cloud_fails(self, tmp_path, capsys):
        rc = eval_main(["polish", "--out", str(tmp_path / "r2"),
                        "--require-gate"])
        assert rc == 1
        assert "gate needs" in capsys.readouterr().err

    def test_gate_fail_exit_code(self, tmp_path, monkeypatch, capsys):
        cases = str(tmp_path / "corpus.toml")
        cases_txt = "".join(
            f'[[case]]\nid = "c{i}"\nraw = "um r{i}"\n'
            f'expected = "Reference {i} sentence."\n'
            for i in range(3))
        Path(cases).write_text(cases_txt, encoding="utf-8")
        monkeypatch.setattr(
            P, "make_local_polisher", lambda: lambda raw: "garbage output")
        monkeypatch.setattr(
            P, "make_endpoint_polisher",
            lambda *a, **k: lambda raw: "Reference sentence.")
        rc = eval_main(["polish", "--manifest", cases,
                        "--out", str(tmp_path / "r3"),
                        "--local", "--cloud-base-url", "http://x/v1",
                        "--cloud-model", "m"])
        assert rc == 1, "a gate regression must exit non-zero"
        assert "gate: FAIL" in capsys.readouterr().out
