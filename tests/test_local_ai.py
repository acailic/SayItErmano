"""Bundled local AI polish (issue #21): llama-server manager lifecycle,
pipeline routing, config keys, chat-GGUF catalog/download — all offline
(loopback fake servers only, per the unit-tier network guard)."""
from __future__ import annotations

import copy
import os
import stat
import threading
import time
import urllib.request

import pytest

from fluidvoice import model_catalog, model_download
from fluidvoice.config import DEFAULTS, coerce_setting
from fluidvoice.local_ai import LocalAIError, LocalAIManager, find_llama_server

FAKE_SERVER = r'''#!/usr/bin/env python3
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

port = int(sys.argv[sys.argv.index("--port") + 1])


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", port), H).serve_forever()
'''

FAKE_SLEEP_SERVER = r'''#!/usr/bin/env python3
import sys
import time
time.sleep(600)
'''


@pytest.fixture()
def cfg():
    return copy.deepcopy(DEFAULTS)


@pytest.fixture()
def ai_cfg(cfg):
    cfg["ai"]["enabled"] = True
    cfg["ai"]["local_polish"] = True
    return cfg


@pytest.fixture()
def chat_model(tmp_path, monkeypatch):
    """Isolated cache with the default chat GGUF 'downloaded'."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    name = model_catalog.CHAT_MODEL_DEFAULT
    p = model_catalog.chat_gguf_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"gguf-bytes")
    return name


@pytest.fixture()
def fake_binary(tmp_path, monkeypatch, script=FAKE_SERVER):
    """A fake llama-server binary (serving /health), env-pinned."""
    binp = tmp_path / "fake-llama-server"
    binp.write_text(script)
    binp.chmod(binp.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("SAYITERMANO_LLAMA_SERVER", str(binp))
    return binp


class TestFindLlamaServer:
    def test_env_override(self, fake_binary):
        assert find_llama_server() == str(fake_binary)

    def test_env_override_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAYITERMANO_LLAMA_SERVER",
                           str(tmp_path / "nope"))
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert find_llama_server() is None

    def test_bundled_cache_location(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SAYITERMANO_LLAMA_SERVER", raising=False)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setattr("shutil.which", lambda name: None)
        bundled = (tmp_path / "sayit-ermano" / "models" / "llama.cpp"
                   / "bin" / "llama-server")
        bundled.parent.mkdir(parents=True)
        bundled.write_text("#!/bin/sh\n")
        bundled.chmod(bundled.stat().st_mode | stat.S_IEXEC)
        assert find_llama_server() == str(bundled)

    def test_path_fallback(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SAYITERMANO_LLAMA_SERVER", raising=False)
        monkeypatch.setattr("shutil.which",
                            lambda name: "/usr/bin/llama-server")
        assert find_llama_server() == "/usr/bin/llama-server"


class TestLocalAIManager:
    def test_disabled_refuses_without_spawning(self, ai_cfg, monkeypatch):
        ai_cfg["ai"]["local_polish"] = False
        m = LocalAIManager(ai_cfg)
        monkeypatch.setattr("subprocess.Popen",
                            lambda *a, **k: pytest.fail("must not spawn"))
        with pytest.raises(LocalAIError, match="local polish is off"):
            m.ensure_ready()
        assert m.status()["running"] is False

    def test_model_missing_is_actionable(self, ai_cfg, tmp_path,
                                         monkeypatch, fake_binary):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))  # empty cache
        m = LocalAIManager(ai_cfg)
        with pytest.raises(LocalAIError, match="Settings"):
            m.ensure_ready()

    def test_binary_missing_is_actionable(self, ai_cfg, chat_model,
                                          monkeypatch):
        monkeypatch.setenv("SAYITERMANO_LLAMA_SERVER", "")
        monkeypatch.setattr("shutil.which", lambda name: None)
        m = LocalAIManager(ai_cfg)
        with pytest.raises(LocalAIError, match="llama-server not found"):
            m.ensure_ready()

    def test_ensure_ready_spawns_and_gets_healthy(self, ai_cfg, chat_model,
                                                  fake_binary):
        m = LocalAIManager(ai_cfg)
        endpoint = m.ensure_ready(timeout=15)
        try:
            assert endpoint["model"] == model_catalog.CHAT_GGUF_CATALOG[
                chat_model]["serve_as"]
            assert endpoint["base_url"].startswith("http://127.0.0.1:")
            assert endpoint["base_url"].endswith("/v1")
            with urllib.request.urlopen(
                    endpoint["base_url"] + "/health", timeout=2) as r:
                assert r.status == 200
            st = m.status()
            assert st["running"] is True
            assert st["cold_start_s"] is not None
            assert st["model_downloaded"] is True
            assert st["last_error"] is None
        finally:
            m.stop()

    def test_second_call_reuses_the_server(self, ai_cfg, chat_model,
                                           fake_binary, monkeypatch):
        m = LocalAIManager(ai_cfg)
        m.ensure_ready(timeout=15)
        launches = []
        real = LocalAIManager._launch_locked

        def counting(self, *a, **k):
            launches.append(1)
            return real(self, *a, **k)

        monkeypatch.setattr(LocalAIManager, "_launch_locked", counting)
        try:
            second = m.ensure_ready(timeout=15)
            assert launches == []  # a healthy child is never respawned
            assert second["base_url"].startswith("http://127.0.0.1:")
        finally:
            m.stop()

    def test_dead_server_is_respawned(self, ai_cfg, chat_model,
                                      fake_binary):
        m = LocalAIManager(ai_cfg)
        m.ensure_ready(timeout=15)
        proc = m._proc
        proc.kill()
        proc.wait(timeout=5)
        second = m.ensure_ready(timeout=15)
        try:
            assert m._proc is not proc  # a fresh child
            assert second["base_url"].startswith("http://127.0.0.1:")
        finally:
            m.stop()

    def test_never_healthy_times_out_with_stderr(self, ai_cfg, chat_model,
                                                 tmp_path, monkeypatch):
        binp = tmp_path / "fake-sleep-server"
        binp.write_text(FAKE_SLEEP_SERVER)
        binp.chmod(binp.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("SAYITERMANO_LLAMA_SERVER", str(binp))
        m = LocalAIManager(ai_cfg)
        with pytest.raises(LocalAIError, match="did not become healthy"):
            m.ensure_ready(timeout=1.0)
        assert m.status()["running"] is False
        assert m.status()["last_error"]

    def test_launch_failure_does_not_wedge_future_calls(
            self, ai_cfg, chat_model, fake_binary, monkeypatch):
        """A failed Popen must clear the in-flight start flag — later
        callers must retry (not wait on a start that will never finish)."""
        m = LocalAIManager(ai_cfg)
        calls = {"n": 0}

        def flaky_launch(self, binary, name):
            calls["n"] += 1
            if calls["n"] == 1:
                raise LocalAIError("failed to launch llama-server: boom")
            return real_launch(self, binary, name)

        real_launch = LocalAIManager._launch_locked
        monkeypatch.setattr(LocalAIManager, "_launch_locked", flaky_launch)
        with pytest.raises(LocalAIError, match="failed to launch"):
            m.ensure_ready(timeout=15)
        assert m.status()["starting"] is False
        # the second call recovers and actually starts the server
        try:
            endpoint = m.ensure_ready(timeout=15)
            assert calls["n"] == 2
            assert endpoint["base_url"].startswith("http://127.0.0.1:")
        finally:
            m.stop()

    def test_maybe_idle_stop(self, ai_cfg, chat_model, fake_binary):
        ai_cfg["ai"]["local_polish_idle_s"] = 30
        now = 1000.0
        m = LocalAIManager(ai_cfg, clock=lambda: now)
        assert m.maybe_idle_stop() is False  # nothing running
        m.ensure_ready(timeout=15)
        try:
            assert m.maybe_idle_stop() is False  # just used
            now += 29.9
            assert m.maybe_idle_stop() is False  # within window
            now += 0.2
            assert m.maybe_idle_stop() is True  # past threshold
            assert m.status()["running"] is False
        finally:
            m.stop()

    def test_idle_disabled_keeps_running(self, ai_cfg, chat_model,
                                         fake_binary):
        ai_cfg["ai"]["local_polish_idle_s"] = 0
        now = 1000.0
        m = LocalAIManager(ai_cfg, clock=lambda: now)
        m.ensure_ready(timeout=15)
        try:
            now += 100000
            assert m.maybe_idle_stop() is False
            assert m.status()["running"] is True
        finally:
            m.stop()

    def test_stop_is_idempotent_and_kills_child(self, ai_cfg, chat_model,
                                                fake_binary):
        m = LocalAIManager(ai_cfg)
        m.ensure_ready(timeout=15)
        pid = m._proc.pid
        m.stop()
        m.stop()
        assert m.status()["running"] is False
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)  # the child was waited on: fully gone

    def test_watch_loop_stops_on_idle(self, ai_cfg, chat_model, fake_binary):
        ai_cfg["ai"]["local_polish_idle_s"] = 30
        m = LocalAIManager(ai_cfg)
        m.ensure_ready(timeout=15)
        stop = threading.Event()
        t = threading.Thread(target=lambda: m.watch_loop(stop), daemon=True)
        t.start()
        try:
            # idle far beyond the 30 s policy with a REAL clock: cheat by
            # rewinding last_used instead of sleeping 30 s
            m.last_used -= 120
            stop.wait(timeout=20)
            assert m.status()["running"] is False
        finally:
            stop.set()
            t.join(timeout=5)
            m.stop()

    def test_status_answers_during_cold_start(self, ai_cfg, chat_model,
                                              tmp_path, monkeypatch):
        """A cold start holds the spawn flow for up to its budget; the
        status surface must stay responsive on another thread (the
        control socket calls it)."""
        binp = tmp_path / "fake-sleep-server"
        binp.write_text(FAKE_SLEEP_SERVER)
        binp.chmod(binp.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("SAYITERMANO_LLAMA_SERVER", str(binp))
        m = LocalAIManager(ai_cfg)
        done = threading.Event()
        error: list = []

        def starter():
            try:
                m.ensure_ready(timeout=3.0)
            except LocalAIError as e:  # expected: never healthy
                error.append(e)
            finally:
                done.set()

        t = threading.Thread(target=starter, daemon=True)
        t.start()
        try:
            time.sleep(0.8)  # the spawn flow is now parked in _await_health
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                st = m.status()  # must NOT block behind the cold start
                assert st["enabled"] is True
                if done.wait(timeout=0.01):
                    break
            assert done.wait(timeout=5)
            assert error  # the starter concluded with the timeout error
            assert m.status()["running"] is False
        finally:
            done.wait(timeout=5)
            t.join(timeout=5)
            m.stop()

    def test_concurrent_ensure_ready_spawns_once(self, ai_cfg, chat_model,
                                                 fake_binary, monkeypatch):
        m = LocalAIManager(ai_cfg)
        launches = []
        real = LocalAIManager._launch_locked

        def counting(self, *a, **k):
            launches.append(1)
            return real(self, *a, **k)

        monkeypatch.setattr(LocalAIManager, "_launch_locked", counting)
        results: list = []
        errs: list = []

        def call():
            try:
                results.append(m.ensure_ready(timeout=20))
            except LocalAIError as e:
                errs.append(e)

        threads = [threading.Thread(target=call) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        try:
            assert not errs
            assert launches == [1]  # exactly ONE child for 3 callers
            assert all(r == results[0] for r in results)
        finally:
            m.stop()


class TestPipelineLocalPolish:
    """DictationPipeline._polish routing through the local manager."""

    def _pipeline(self, cfg, backend):
        from fluidvoice.daemon import DictationPipeline
        return DictationPipeline(cfg, backend)

    def test_local_polish_routes_to_endpoint(self, ai_cfg, monkeypatch):
        from fluidvoice import daemon as dm
        seen = {}

        class FakeClient:
            def __init__(self, cfg):
                seen["cfg"] = cfg

            def polish(self, text, system_prompt=None):
                return f"local({text})"

        monkeypatch.setattr(dm, "AIClient", FakeClient)
        manager = LocalAIManager(ai_cfg)
        pipe = self._pipeline(ai_cfg, type("B", (), {"name": "stub"})())
        pipe._local_ai = manager
        monkeypatch.setattr(manager, "ensure_ready",
                            lambda timeout=None: {
                                "base_url": "http://127.0.0.1:9/v1",
                                "model": "qwen3-4b"})
        text, ai_used = pipe._polish("um hi")
        assert ai_used is True
        assert text == "local(um hi)"
        assert seen["cfg"]["ai"]["base_url"] == "http://127.0.0.1:9/v1"
        assert seen["cfg"]["ai"]["model"] == "qwen3-4b"
        assert seen["cfg"]["ai"]["api_key"] == ""
        assert seen["cfg"]["ai"]["api_key_env"] == ""
        # the user's cloud endpoint must NOT be sent to (privacy promise)
        assert seen["cfg"]["ai"]["base_url"] != ai_cfg["ai"]["base_url"]

    def test_local_error_falls_back_to_raw(self, ai_cfg, monkeypatch):
        from fluidvoice import daemon as dm
        monkeypatch.setattr(
            dm, "AIClient",
            lambda cfg: pytest.fail("no request may be attempted"))
        manager = LocalAIManager(ai_cfg)
        pipe = self._pipeline(ai_cfg, type("B", (), {"name": "stub"})())
        pipe._local_ai = manager

        def boom(timeout=None):
            raise LocalAIError("model not downloaded")

        monkeypatch.setattr(manager, "ensure_ready", boom)
        text, ai_used = pipe._polish("raw words")
        assert (text, ai_used) == ("raw words", False)

    def test_local_on_but_no_manager_falls_back_to_raw(self, ai_cfg):
        pipe = self._pipeline(ai_cfg, type("B", (), {"name": "stub"})())
        text, ai_used = pipe._polish("raw words")
        assert (text, ai_used) == ("raw words", False)

    def test_local_off_uses_configured_endpoint(self, cfg, monkeypatch):
        cfg["ai"]["enabled"] = True
        cfg["ai"]["local_polish"] = False
        from fluidvoice import daemon as dm
        seen = {}

        class FakeClient:
            def __init__(self, c):
                seen["cfg"] = c

            def polish(self, text, system_prompt=None):
                return "cloud()"

        monkeypatch.setattr(dm, "AIClient", FakeClient)
        pipe = self._pipeline(cfg, type("B", (), {"name": "stub"})())
        pipe._local_ai = LocalAIManager(cfg)
        text, ai_used = pipe._polish("hi")
        assert (text, ai_used) == ("cloud()", True)
        assert seen["cfg"]["ai"]["base_url"] == cfg["ai"]["base_url"]

    def test_ai_disabled_skips_everything(self, ai_cfg):
        ai_cfg["ai"]["enabled"] = False
        pipe = self._pipeline(ai_cfg, type("B", (), {"name": "stub"})())
        pipe._local_ai = LocalAIManager(ai_cfg)
        text, ai_used = pipe._polish("hi")
        assert (text, ai_used) == ("hi", False)


class TestConfigKeys:
    def test_defaults(self):
        assert DEFAULTS["ai"]["local_polish"] is False
        assert DEFAULTS["ai"]["local_polish_model"] == \
            model_catalog.CHAT_MODEL_DEFAULT
        assert DEFAULTS["ai"]["local_polish_idle_s"] == 300

    def test_bool_coercion(self):
        assert coerce_setting("ai", "local_polish", True) == (True, True)
        assert coerce_setting("ai", "local_polish", "on")[0] is False

    def test_model_catalog_only(self):
        ok, v = coerce_setting("ai", "local_polish_model",
                               model_catalog.CHAT_MODEL_DEFAULT)
        assert ok and v == model_catalog.CHAT_MODEL_DEFAULT
        assert coerce_setting("ai", "local_polish_model", "gpt-5")[0] is False
        # empty normalizes to the default entry
        ok, v = coerce_setting("ai", "local_polish_model", "")
        assert ok and v == model_catalog.CHAT_MODEL_DEFAULT

    def test_idle_bounds(self):
        for good in (0, 30, 300, 86400):
            assert coerce_setting("ai", "local_polish_idle_s", good) == \
                (True, good)
        for bad in (29, 86401, -1, True, "300"):
            assert coerce_setting("ai", "local_polish_idle_s", bad)[0] is False


class TestChatCatalog:
    def test_paths_and_presence(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        name = model_catalog.CHAT_MODEL_DEFAULT
        assert model_catalog.chat_gguf_downloaded(name) is False
        p = model_catalog.chat_gguf_path(name)
        p.parent.mkdir(parents=True)
        p.write_bytes(b"x")
        assert model_catalog.chat_gguf_downloaded(name) is True
        assert model_catalog.chat_gguf_downloaded("nope.gguf") is False

    def test_cached_models_lists_llama_kind(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        name = model_catalog.CHAT_MODEL_DEFAULT
        p = model_catalog.chat_gguf_path(name)
        p.parent.mkdir(parents=True)
        p.write_bytes(b"x" * 10)
        entries = model_catalog.cached_models()
        mine = [e for e in entries if e["kind"] == "llama.cpp"]
        assert len(mine) == 1
        assert mine[0]["name"] == name
        assert mine[0]["bytes"] == 10

    def test_cache_entry_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        p = model_catalog.cache_entry_path(
            "llama.cpp", model_catalog.CHAT_MODEL_DEFAULT)
        assert p == model_catalog.chat_gguf_path(
            model_catalog.CHAT_MODEL_DEFAULT)
        with pytest.raises(ValueError, match="llama.cpp"):
            model_catalog.cache_entry_path("huggingface", "x")

    def test_catalog_entry_shape(self):
        for name, info in model_catalog.CHAT_GGUF_CATALOG.items():
            assert info["url"].startswith("https://")
            assert info["serve_as"]
            assert int(info["ctx"]) >= 512
            assert name.endswith(".gguf")


class TestDownloadChatGGUF:
    def test_unknown_name_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        with pytest.raises(ValueError, match="chat gguf"):
            model_download.download_chat_gguf("ggml-base.bin")

    def test_happy_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        name = model_catalog.CHAT_MODEL_DEFAULT
        import urllib.request

        class FakeResp:
            headers = {"Content-Length": "4"}

            def __init__(self):
                self._served = False

            def read(self, n=None):
                if self._served:
                    return b""
                self._served = True
                return b"abcd"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(urllib.request, "urlopen",
                            lambda req, timeout=None: FakeResp())
        dest = model_download.download_chat_gguf(name)
        assert dest == model_catalog.chat_gguf_path(name)
        assert dest.read_bytes() == b"abcd"

    def test_existing_file_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        name = model_catalog.CHAT_MODEL_DEFAULT
        p = model_catalog.chat_gguf_path(name)
        p.parent.mkdir(parents=True)
        p.write_bytes(b"already")
        import urllib.request
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **k: pytest.fail("must not download"))
        assert model_download.download_chat_gguf(name) == p

    def test_part_file_observed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        name = model_catalog.CHAT_MODEL_DEFAULT
        pdir = model_catalog.chat_gguf_dir()
        pdir.mkdir(parents=True)
        (pdir / (name + ".part")).write_bytes(b"12345")
        obs = [d for d in model_download.observe_downloads()
               if d.kind == "llama.cpp" and d.label == name]
        assert len(obs) == 1
        assert obs[0].total_bytes is not None  # catalog estimate attached


class _StubRecorder:
    def start(self, path):
        pass

    def stop(self):
        return None


class TestDaemonWiring:
    def _daemon(self, cfg):
        from fluidvoice import daemon as dm
        return dm.Daemon(cfg=cfg, recorder=_StubRecorder(),
                         use_hotkey=False, use_sounds=False)

    def test_status_route_carries_local_ai_block(self, cfg):
        d = self._daemon(cfg)
        resp = d.handle_request({"action": "status"})
        assert resp["ok"] is True
        assert resp["local_ai"]["enabled"] is False
        assert resp["local_ai"]["running"] is False

    def test_toggle_on_starts_watcher(self, cfg):
        cfg["ai"]["local_polish"] = True
        d = self._daemon(cfg)
        d._start_local_ai_watch()
        try:
            assert d._local_ai_watch is not None
            assert d._local_ai_watch.is_alive()
        finally:
            d._local_ai_stop.set()

    def test_toggle_off_stops_server_and_watcher(self, cfg):
        cfg["ai"]["local_polish"] = True
        d = self._daemon(cfg)
        stopped = []
        d._local_ai.stop = lambda: stopped.append(1)
        d._start_local_ai_watch()
        watcher = d._local_ai_watch
        d.cfg["ai"]["local_polish"] = False
        out = d.apply_config(["ai.local_polish"])
        try:
            assert "local polish" in out["applied"]
            assert stopped == [1]  # zero footprint: server stopped at once
            assert d._local_ai_watch is None
            watcher.join(timeout=5)
            assert not watcher.is_alive()
        finally:
            d._local_ai_stop.set()

    def test_run_starts_watch_only_when_enabled(self, cfg):
        d = self._daemon(cfg)  # local_polish defaults to False
        d._start_local_ai_watch()
        assert d._local_ai_watch is None  # nothing runs when off


class TestDeleteGuard:
    def _manager(self, cfg):
        from fluidvoice.engine_manager import SpeechEngineManager
        from fluidvoice.runtime_tasks import RuntimeTasks
        return SpeechEngineManager(
            cfg, RuntimeTasks(), backend_factory=lambda c: None,
            lock=threading.Lock(), log=lambda msg: None)

    def test_active_local_polish_model_refused(self, cfg, tmp_path,
                                               monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        name = model_catalog.CHAT_MODEL_DEFAULT
        p = model_catalog.chat_gguf_path(name)
        p.parent.mkdir(parents=True)
        p.write_bytes(b"gguf")
        cfg["ai"]["local_polish"] = True
        cfg["ai"]["local_polish_model"] = name
        out = self._manager(cfg).delete_model("llama.cpp", name)
        assert out["ok"] is False
        assert "local-polish" in out["error"]

    def test_deletion_allowed_when_local_polish_off(self, cfg, tmp_path,
                                                    monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        name = model_catalog.CHAT_MODEL_DEFAULT
        p = model_catalog.chat_gguf_path(name)
        p.parent.mkdir(parents=True)
        p.write_bytes(b"gguf")
        cfg["ai"]["local_polish"] = False
        out = self._manager(cfg).delete_model("llama.cpp", name)
        assert out["ok"] is True and not p.exists()
