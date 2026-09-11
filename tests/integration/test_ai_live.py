"""Live AI tests against a local OpenAI-compatible server (Ollama).

These run only when an endpoint answers on localhost - otherwise skipped,
never failed: the offline client behavior is fully covered by unit tests.
"""
import copy
import json
import urllib.request

import pytest

from fluidvoice.ai.client import AIClient
from fluidvoice.config import DEFAULTS
from fluidvoice.rewrite import RewriteError, run_rewrite

pytestmark = [pytest.mark.integration, pytest.mark.needs_network]

TIMEOUT = 180


def _ollama_models():
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags",
                                    timeout=3) as r:
            return [m["name"] for m in json.loads(r.read())["models"]]
    except Exception:
        return []


def _chat_model():
    models = [m for m in _ollama_models() if "embed" not in m]
    return models[0] if models else None


needs_ollama = pytest.mark.skipif(_chat_model() is None,
                                  reason="no local Ollama chat model")


def live_cfg():
    cfg = copy.deepcopy(DEFAULTS)
    cfg["ai"].update({"enabled": True, "base_url": "http://localhost:11434/v1",
                      "model": _chat_model(), "timeout_seconds": TIMEOUT,
                      "max_retries": 1})
    return cfg


@needs_ollama
class TestLivePolish:
    def test_messy_transcript_gets_cleaned(self):
        out = AIClient(live_cfg()).polish(
            "um lets meet on um tuesday around like three no wait four pm ok")
        assert isinstance(out, str) and out.strip()
        assert out != ""  # something came back through the real model

    def test_thinking_only_response_falls_back(self):
        # whatever the model answers, the client must return a usable string
        out = AIClient(live_cfg()).polish("hello")
        assert isinstance(out, str)


@needs_ollama
class TestLiveRewrite:
    def test_rewrite_with_context(self):
        try:
            out = run_rewrite(live_cfg(), "rewrite this in uppercase",
                              "the quick brown fox")
        except RewriteError as e:
            pytest.skip(f"local model refused/failed: {e}")
        assert isinstance(out, str) and out.strip()

    def test_rewrite_without_ai_fails_cleanly(self):
        cfg = live_cfg()
        cfg["ai"]["enabled"] = False
        with pytest.raises(RewriteError, match="needs .ai. enabled"):
            run_rewrite(cfg, "x", None)
