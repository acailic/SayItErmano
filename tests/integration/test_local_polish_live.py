"""Live local-polish E2E (#21): real llama.cpp server + real GGUF chat
model, network disconnected for the polish call itself.

Runs only when BOTH prerequisites exist on the machine (skip, never
fail, like the Ollama live tests): an ``llama-server`` binary on PATH
and the bundled chat model already in the managed cache (download it
first — Settings → Models → AI polish model, or
``model_download.download_chat_model(CHAT_MODEL_DEFAULT)``). The model
download itself needs the network, but the polish path under test here
must work with networking disabled once the model is local: the manager
only ever talks to its own loopback child.

CPU-only machines are the design target (the acceptance criterion).
"""
from __future__ import annotations

import copy
import shutil

import pytest

from fluidvoice import local_llm, model_catalog
from fluidvoice.config import DEFAULTS

pytestmark = [pytest.mark.integration, pytest.mark.needs_model]

MODEL = model_catalog.CHAT_MODEL_DEFAULT

needs_llama = pytest.mark.skipif(
    shutil.which("llama-server") is None,
    reason="no llama-server binary on PATH")
needs_model_file = pytest.mark.skipif(
    not model_catalog.chat_model_downloaded(MODEL),
    reason=f"chat model {MODEL} not downloaded")


def local_cfg() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    cfg["ai"].update({"local_polish": True, "enabled": False,
                      "local_idle_s": 60})
    return cfg


@needs_llama
@needs_model_file
class TestLiveLocalPolish:
    def test_dictate_to_polished_text_offline(self, tmp_path):
        """The E2E acceptance: transcript in -> polished text out, with
        the only endpoint the daemon's own loopback child. The fake
        'network disconnected' posture: nothing outside 127.0.0.1 is
        ever contacted by the polish path (the manager spawns and calls
        its own child; ai.api_key stays empty)."""
        from fluidvoice.runtime_tasks import RuntimeTasks
        logs: list[str] = []
        mgr = local_llm.LocalPolishManager(
            local_cfg(), RuntimeTasks(), log=logs.append)
        try:
            out = mgr.polish_text(
                "um lets meet on um tuesday around like three "
                "no wait four pm ok")
            assert isinstance(out, str) and out.strip()
            assert "tuesday" in out.lower()
            # cold-start budget honesty: readiness within the documented
            # startup window (docs/dev/local-polish-budgets.md: 90 s cap,
            # 5-20 s typical CPU)
            spawn_logs = [ln for ln in logs if "spawning" in ln]
            ready_logs = [ln for ln in logs if "ready on" in ln]
            assert spawn_logs and ready_logs
            # idle-down policy armed (default 60 s in this cfg)
            assert mgr.idle_threshold() == 60
            status = mgr.status()
            assert status["running"] and status["port"]
        finally:
            mgr.shutdown()
        assert mgr.status()["running"] is False  # zero footprint after

    def test_idle_stop_frees_the_process(self, tmp_path):
        from fluidvoice.runtime_tasks import RuntimeTasks
        mgr = local_llm.LocalPolishManager(
            local_cfg(), RuntimeTasks(), log=lambda m: None)
        try:
            mgr.ensure_running()
            proc = mgr._proc
            # 61 s of idleness (one past the 60 s policy), faked through
            # the injectable now — no wall-clock sleeping in tests
            mgr.maybe_idle_stop(now=mgr.last_polish + 61)
            assert proc is not None and proc.poll() is not None
        finally:
            mgr.shutdown()

    def test_polish_compare_adapter_smoke(self, tmp_path):
        """The eval-harness local adapter path against the real server
        (corpus of one hand-written case; the full gated comparison is a
        release-time manual run — docs/eval/README.md)."""
        from fluidvoice.evalharness.manifest import Case, SynthSpec
        from fluidvoice.evalharness.polish_gate import run_polish_eval
        from fluidvoice.runtime_tasks import RuntimeTasks
        mgr = local_llm.LocalPolishManager(
            local_cfg(), RuntimeTasks(), log=lambda m: None)
        try:
            def local_polish(text, case):
                return {"text": mgr.polish_text(text)}

            def asr(audio, case):
                # stand-in raw ASR output with the usual disfluencies
                return {"text": case.reference_text + " um"}

            case = Case(
                id="live1",
                audio=tmp_path / "live1.wav",
                reference_text="Lets meet on Tuesday at 4 p.m.",
                language="en", tags=("tuesday",), expected_guard="none",
                license="CC0-1.0", source="hand-written",
                synth=SynthSpec(kind="tone", seconds=0.1))
            (tmp_path / "live1.wav").write_bytes(b"")
            report = run_polish_eval(
                [case], {"local": local_polish}, transcriber=asr,
                out_dir=tmp_path / "out", manifests=[tmp_path],
                gate=None)
            s = report["summary"]["local"]
            assert s["errors"] == 0
            # the real model must not LOSE the hotword
            assert (s["hotword_recall_mean"] or 0.0) >= 0.99
        finally:
            mgr.shutdown()
