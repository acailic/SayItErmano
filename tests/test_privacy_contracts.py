"""Privacy and command-safety regression contracts (Q11).

Focused BEHAVIORAL contracts, fake boundaries only — no real credentials
or recordings anywhere near these tests:

* LOCAL MODE STAYS LOCAL: with default (local) config, a full dictation
  pipeline run never invokes the AI client, never resolves a remote STT
  backend, and the conftest unit-tier network guard would fail the test
  on any non-loopback connect attempted along the way;
* SECRETS STAY MASKED across the surfaces a user can actually see:
  socket `status`, socket `get_config`, and the `doctor` CLI output of a
  real subprocess (update check kill-switched);
* DEFAULT RETENTION IS NOTHING: a dictation retains no audio file unless
  history.save_audio opts in, and a dictation session writes no context
  artifacts to disk (history.jsonl is the only new data file);
* unconfirmed/denied command proposals never execute — heavily pinned
  already in test_command.py / test_command_coord.py (single/double
  press, Escape, timeout); those are the contracts, not duplicated here.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fluidvoice import backends, paths
from fluidvoice import daemon as dm
from fluidvoice.ai import client as ai_client
from fluidvoice.config import DEFAULTS
from fluidvoice.pipeline import DictationPipeline
from tests.test_daemon import StubBackend, StubRecorder, make_wav

REPO = Path(__file__).resolve().parents[1]
SECRET_AI = "sk-live-SECRET-ai-key-0123456789abcdef"
SECRET_REMOTE = "SECRET-remote-stt-key-XYZ"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "history_file",
                        lambda: tmp_path / "xdata" / "history.jsonl")
    monkeypatch.setattr(paths, "audio_dir",
                        lambda: tmp_path / "xdata" / "audio")
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "xdata")
    calls = {"notify": []}
    monkeypatch.setattr(dm.ui, "notify",
                        lambda t, b="", **k: calls["notify"].append((t, b)))
    monkeypatch.setattr(dm.ui, "play_sound", lambda *a, **k: None)
    return tmp_path, calls


def run_dictation(cfg, tmp_path, text="hello world") -> dict:
    wav = make_wav(tmp_path / "utt.wav")
    pipe = DictationPipeline(cfg, StubBackend(text),
                             inserter=lambda t, c: "typed")
    out = pipe.run(wav, "TestApp")
    return out or {}


class TestLocalModeStaysLocal:
    def test_ai_client_never_invoked_when_disabled(self, isolated):
        """Default config: AI polish off. If anything in the pipeline
        were to reach for the AI client anyway, the spy raises and the
        conftest network guard would catch any real HTTP attempt."""
        tmp_path, _ = isolated
        cfg = copy.deepcopy(DEFAULTS)
        assert cfg["ai"]["enabled"] is False  # the default under test

        def must_not_be_called(*a, **k):
            raise AssertionError("AI client invoked while ai.enabled=False")

        # patch the CLASS method: the pipeline constructs its own client
        orig = ai_client.AIClient.chat
        ai_client.AIClient.chat = must_not_be_called
        try:
            out = run_dictation(cfg, tmp_path)
            assert out["text"] == "hello world"
        finally:
            ai_client.AIClient.chat = orig

    def test_default_backend_resolution_is_local_not_remote(
            self, isolated, monkeypatch):
        """With no remote STT configured, backend selection must never
        construct the remote adapter (the only adapter with a URL)."""
        cfg = copy.deepcopy(DEFAULTS)
        assert cfg["model"].get("remote_url", "") == ""  # remote is OFF
        constructed = []
        import fluidvoice.backends.remote_stt as remote_mod
        orig_init = remote_mod.RemoteSttBackend.__init__

        def spy_init(self, *a, **k):
            constructed.append(1)
            orig_init(self, *a, **k)

        remote_mod.RemoteSttBackend.__init__ = spy_init
        try:
            backend = backends.load_backend(cfg)
            backend_name = backend.name
        finally:
            remote_mod.RemoteSttBackend.__init__ = orig_init
        assert constructed == []
        assert "remote" not in backend_name.lower()


class TestSecretsStayMasked:
    def _secret_cfg(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["ai"]["enabled"] = False
        cfg["ai"]["api_key"] = SECRET_AI
        cfg["model"]["backend"] = "remote"
        cfg["model"]["remote_api_key"] = SECRET_REMOTE
        cfg["model"]["remote_url"] = "https://stt.example.internal/v1"
        cfg["model"]["remote_model"] = "some-remote-model"
        return cfg

    def test_socket_status_and_config_never_contain_secrets(self, isolated):
        cfg = self._secret_cfg()
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: StubBackend("x"),
                      use_hotkey=False, use_sounds=False)
        d.backend = StubBackend("x")
        for surface in (json.dumps(d.handle_request({"action": "status"})),
                        json.dumps(d.handle_request({"action": "get_config"}))):
            assert SECRET_AI not in surface
            assert SECRET_REMOTE not in surface

    def test_doctor_cli_output_never_contains_secrets(self, tmp_path):
        """A real `python -m fluidvoice doctor` subprocess with secrets
        configured: stdout/stderr must not leak either key (update probe
        kill-switched; a doctor that cannot reach the mic still runs)."""
        xdg = tmp_path / "xdata"
        (xdg / "sayit-ermano").mkdir(parents=True)
        cfg = self._secret_cfg()
        from fluidvoice.config import save_config
        save_config(cfg, xdg / "sayit-ermano" / "config.toml")
        env = {**os.environ,
               "XDG_DATA_HOME": str(xdg),
               "XDG_CONFIG_HOME": str(tmp_path / "xconf"),
               "XDG_CACHE_HOME": str(tmp_path / "xcache"),
               "SAYITERMANO_SKIP_UPDATE_CHECK": "1"}
        env.pop("SAYITERMANO_CONFIG", None)
        proc = subprocess.run(
            [sys.executable, "-m", "fluidvoice", "doctor"],
            cwd=str(REPO), env=env, capture_output=True, text=True,
            timeout=120)
        combined = proc.stdout + proc.stderr
        assert SECRET_AI not in combined, (
            "doctor leaked the AI api_key:\n" + combined[-800:])
        assert SECRET_REMOTE not in combined, (
            "doctor leaked the remote STT key:\n" + combined[-800:])


class TestDefaultRetentionIsNothing:
    def test_no_audio_retained_and_no_context_files_by_default(
            self, isolated):
        tmp_path, _ = isolated
        cfg = copy.deepcopy(DEFAULTS)
        assert cfg["history"].get("save_audio") is False  # default under test
        out = run_dictation(cfg, tmp_path)
        assert out["text"] == "hello world"
        data_dir = tmp_path / "xdata"
        audio = list((data_dir / "audio").glob("*")) \
            if (data_dir / "audio").exists() else []
        assert audio == [], f"audio retained by default: {audio}"
        files = sorted(p.name for p in data_dir.rglob("*") if p.is_file())
        # history.jsonl.lock is the flock sidecar (empty, expected)
        assert files == ["history.jsonl", "history.jsonl.lock"], (
            f"dictation wrote unexpected data artifacts: {files}")

    def test_opt_in_audio_retention_retains_the_take(self, isolated):
        tmp_path, _ = isolated
        cfg = copy.deepcopy(DEFAULTS)
        cfg["history"]["save_audio"] = True
        run_dictation(cfg, tmp_path)
        wavs = list((tmp_path / "xdata" / "audio").glob("*.wav"))
        assert len(wavs) == 1
        # and the history row references exactly that file
        rows = [json.loads(l) for l in
                (tmp_path / "xdata" / "history.jsonl").read_text().splitlines()
                if l.strip()]
        assert rows and rows[0].get("audio") == str(wavs[0])
