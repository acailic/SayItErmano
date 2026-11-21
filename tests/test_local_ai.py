"""Bundled local AI polish (issue #21): catalog + downloads + the
supervised llama.cpp server + daemon/socket wiring.

Everything here runs offline: downloads go through faked urlopen
(test_model_download pattern) and the server supervisor spawns a STUB
HTTP server (stdlib http.server on 127.0.0.1) — the conftest network
guard permits loopback, and every spawned process is stopped before the
test ends (the Popen leak gate enforces it).
"""
from __future__ import annotations

import io
import sys
import tarfile
import time
from pathlib import Path

import pytest

from fluidvoice import model_catalog, model_download
from fluidvoice.ai import local as L
from fluidvoice.ai.client import AIClient
from fluidvoice.config import DEFAULTS, coerce_setting
from fluidvoice.model_download import DownloadCancelled  # noqa: F401 - API surface


@pytest.fixture()
def quiet_ui(tmp_path, monkeypatch):
    """Silence notifications/sounds and isolate history (test_daemon
    pattern) - the daemon notify path runs on ensure failures."""
    from fluidvoice import daemon as dm
    calls = {"notify": []}

    def fake_notify(title, body="", timeout_ms=2500, enabled=True):
        if enabled:
            calls["notify"].append((title, body))

    monkeypatch.setattr(dm.ui, "notify", fake_notify)
    monkeypatch.setattr(dm.history_mod.paths, "history_file",
                        lambda: tmp_path / "test-history.jsonl")
    return calls

# -- stub server ------------------------------------------------------------

STUB_SERVER = r"""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port = int(sys.argv[sys.argv.index("--port") + 1])

class H(BaseHTTPRequestHandler):
    def _send(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, b'{"status":"ok"}')
        else:
            self._send(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        self._send(200, json.dumps({"choices": [
            {"message": {"content": "Stub polished text."}}]}).encode())

    def log_message(self, *a):
        pass

ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
"""

DIE_SERVER = r"""
import sys, time
time.sleep(30)
"""


def free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def write_stub(tmp_path: Path, source: str = STUB_SERVER) -> Path:
    p = tmp_path / "stub-server.py"
    p.write_text(source, encoding="utf-8")
    return p


def make_server(cfg_ai: dict, tmp_path: Path, *, source: str = STUB_SERVER,
                **kw):
    cfg = {"ai": {"enabled": True, "local_polish": True, **cfg_ai}}
    port = cfg_ai.get("_port") or free_port()
    cfg["ai"]["local_port"] = port
    stub = write_stub(tmp_path, source)
    srv = L.LocalServer(
        cfg, command=[sys.executable, "-u", str(stub)], **kw)
    return srv, cfg, port


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def place_model(name: str = model_catalog.CHAT_DEFAULT_MODEL) -> None:
    p = model_catalog.chat_model_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"gguf-bytes")


def place_runtime() -> None:
    p = model_catalog.llama_server_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"#!/bin/sh\n")
    p.chmod(0o755)


# -- catalog -----------------------------------------------------------------


class TestCatalog:
    def test_chat_paths_live_in_managed_cache(self, cache):
        p = model_catalog.chat_model_path(model_catalog.CHAT_DEFAULT_MODEL)
        assert p.is_relative_to(model_catalog.chat_dir())
        assert p.parent.parent == model_catalog.paths.models_dir()

    def test_chat_model_downloaded(self, cache):
        name = model_catalog.CHAT_DEFAULT_MODEL
        assert not model_catalog.chat_model_downloaded(name)
        place_model(name)
        assert model_catalog.chat_model_downloaded(name)

    def test_runtime_paths_and_probe(self, cache):
        assert not model_catalog.llama_runtime_downloaded()
        place_runtime()
        assert model_catalog.llama_runtime_downloaded()
        assert model_catalog.llama_server_path().is_relative_to(
            model_catalog.llama_runtime_dir())

    def test_runtime_spec_pinned_with_digest(self):
        spec = model_catalog.llama_runtime_spec()
        assert spec["url"].startswith(model_catalog.LLAMA_CPP_RELEASE_BASE)
        assert len(spec["sha256"]) == 64

    def test_cached_models_and_delete_paths(self, cache):
        place_model()
        place_runtime()
        entries = {(e["kind"], e["name"]): e for e in
                   model_catalog.cached_models()}
        file = model_catalog.CHAT_CATALOG[
            model_catalog.CHAT_DEFAULT_MODEL]["file"]
        assert ("llama.cpp", file) in entries
        assert ("llama.cpp-runtime",
                f"llama-server ({model_catalog.LLAMA_CPP_TAG})") in entries
        for (kind, name), e in entries.items():
            assert model_catalog.cache_entry_path(kind, name) == e["path"]
        with pytest.raises(ValueError):
            model_catalog.cache_entry_path("llama.cpp-runtime", "other (x)")


# -- downloads ---------------------------------------------------------------


class FakeResp:
    def __init__(self, body: bytes, length: int | None = None):
        self._body = body
        self.headers = {"Content-Length": str(length if length is not None
                                              else len(body))}

    def read(self, n):
        out, self._body = self._body[:n], self._body[n:]
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_tarball(files: dict[str, bytes], mode: int = 0o644,
                 exec_bits: tuple[str, ...] = ()) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, data in files.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            info.mode = 0o755 if path in exec_bits else mode
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _tar_asset(files: dict[str, bytes],
               exec_bits: tuple[str, ...] = ()) -> bytes:
    tag = model_catalog.LLAMA_CPP_TAG
    prefixed = {f"llama-{tag}/{k}": v for k, v in files.items()}
    return make_tarball(prefixed,
                        exec_bits=tuple(f"llama-{tag}/{k}"
                                        for k in exec_bits))


class TestDownloads:
    def _patch_fetch(self, monkeypatch, bodies: list[bytes]):
        import urllib.request
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            return FakeResp(bodies.pop(0))

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        return calls

    def test_download_chat_model_verifies_sha(self, cache, monkeypatch):
        name = model_catalog.CHAT_DEFAULT_MODEL
        want = model_catalog.CHAT_CATALOG[name]["sha256"]
        # craft bytes whose sha256 IS the pin: monkeypatch sha256_file
        # instead of finding a preimage - the pin check itself is under test
        seen = []

        def fake_sha(path):
            seen.append(Path(path))
            return want

        monkeypatch.setattr(model_download, "sha256_file", fake_sha)
        self._patch_fetch(monkeypatch, [b"gguf-bytes"])
        dest = model_download.download_chat_model(name)
        assert dest == model_catalog.chat_model_path(name)
        assert dest.read_bytes() == b"gguf-bytes"
        assert seen == [dest]

    def test_download_chat_model_bad_sha_cleans_up(self, cache, monkeypatch):
        name = model_catalog.CHAT_DEFAULT_MODEL
        monkeypatch.setattr(model_download, "sha256_file",
                            lambda p: "0" * 64)
        self._patch_fetch(monkeypatch, [b"gguf-bytes"])
        with pytest.raises(OSError, match="checksum mismatch"):
            model_download.download_chat_model(name)
        assert not model_catalog.chat_model_path(name).exists()
        assert not list(model_catalog.chat_dir().glob("*.part"))

    def test_download_chat_model_unknown_name(self, cache):
        with pytest.raises(ValueError, match="unknown chat model"):
            model_download.download_chat_model("gpt-5")

    def test_download_runtime_extracts_and_execs(self, cache, monkeypatch):
        tag = model_catalog.LLAMA_CPP_TAG
        spec = model_catalog.llama_runtime_spec()
        body = _tar_asset({"llama-server": b"ELF", "libllama.so.1": b"LIB",
                           "README.md": b"doc"},
                          exec_bits=("llama-server",))
        monkeypatch.setattr(model_download, "sha256_file",
                            lambda p: spec["sha256"])
        self._patch_fetch(monkeypatch, [body])
        final = model_download.download_llama_runtime()
        assert final == model_catalog.llama_runtime_dir()
        server = model_catalog.llama_server_path()
        assert server.read_bytes() == b"ELF"
        assert server.stat().st_mode & 0o111, "binary must be executable"
        assert (final / f"llama-{tag}" / "libllama.so.1").read_bytes() == b"LIB"
        assert model_catalog.llama_runtime_downloaded()

    def test_download_runtime_refuses_traversal(self, cache, monkeypatch):
        spec = model_catalog.llama_runtime_spec()
        body = make_tarball({"llama-x/../../evil.sh": b"EVIL"})
        monkeypatch.setattr(model_download, "sha256_file",
                            lambda p: spec["sha256"])
        self._patch_fetch(monkeypatch, [body])
        with pytest.raises(OSError, match="no llama-server"):
            model_download.download_llama_runtime()
        assert not model_catalog.llama_runtime_dir().exists()

    def test_download_runtime_bad_digest(self, cache, monkeypatch):
        monkeypatch.setattr(model_download, "sha256_file",
                            lambda p: "f" * 64)
        self._patch_fetch(monkeypatch, [_tar_asset({"llama-server": b"ELF"})])
        with pytest.raises(OSError, match="checksum mismatch"):
            model_download.download_llama_runtime()
        assert not model_catalog.llama_runtime_dir().exists()

    def test_download_chat_stack_skips_present_pieces(self, cache,
                                                      monkeypatch):
        import urllib.request
        place_model()
        place_runtime()
        fetches = []
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda req, timeout=None: fetches.append(req.full_url))
        model_download.download_chat_stack(
            model_catalog.CHAT_DEFAULT_MODEL)
        assert fetches == []  # everything cached: zero network

    def test_download_chat_stack_fetches_both(self, cache, monkeypatch):
        import urllib.request
        name = model_catalog.CHAT_DEFAULT_MODEL
        spec = model_catalog.llama_runtime_spec()
        body = _tar_asset({"llama-server": b"ELF"})
        seen_progress = []

        def fake_urlopen(req, timeout=None):
            return FakeResp(body if "llama-b" in req.full_url else b"model")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(model_download, "sha256_file",
                            lambda p: spec["sha256"]
                            if "llama-b" in str(p) else
                            model_catalog.CHAT_CATALOG[name]["sha256"])
        model_download.download_chat_stack(
            name, progress=lambda b, t: seen_progress.append((b, t)))
        assert model_catalog.chat_model_downloaded(name)
        assert model_catalog.llama_runtime_downloaded()
        assert seen_progress, "progress must be reported"
        assert seen_progress[-1][1] is not None, "aggregate total expected"


# -- LocalServer lifecycle ----------------------------------------------------


class TestLocalServer:
    def test_zero_footprint_when_off(self, tmp_path):
        srv, cfg, port = make_server(
            {"local_polish": False, "enabled": True}, tmp_path)
        assert srv.status()["enabled"] is False
        with pytest.raises(L.LocalServerError, match="off"):
            srv.ensure_ready()
        assert srv.status()["running"] is False

    def test_missing_model_is_actionable_no_spawn(self, tmp_path, cache):
        srv, cfg, port = make_server({}, tmp_path)
        with pytest.raises(L.LocalServerError, match="not downloaded"):
            srv.ensure_ready(timeout=2)
        assert srv.status()["running"] is False

    def test_ensure_ready_spawns_and_is_idempotent(self, tmp_path, cache):
        place_model()
        srv, cfg, port = make_server({}, tmp_path)
        try:
            ov = srv.ensure_ready(timeout=10)
            assert ov["base_url"] == f"http://127.0.0.1:{port}/v1"
            assert ov["model"] == model_catalog.CHAT_DEFAULT_MODEL
            assert ov["api_key"] == ""
            st = srv.status()
            assert st["running"] and st["pid"] and st["port"] == port
            assert st["model_downloaded"] and st["runtime_downloaded"] is False
            first_pid = st["pid"]
            ov2 = srv.ensure_ready(timeout=10)
            assert ov2 == ov
            assert srv.status()["pid"] == first_pid  # no respawn
        finally:
            srv.stop("test")
        assert srv.status()["running"] is False

    def test_polish_through_stub_server(self, tmp_path, cache):
        """The whole point: AIClient with local_polish on talks to the
        spawned server end to end (loopback only)."""
        place_model()
        srv, cfg, port = make_server({}, tmp_path)
        try:
            cfg["ai"]["base_url"] = "https://cloud.example/v1"
            cfg["ai"]["api_key"] = "sk-cloud"
            srv.ensure_ready(timeout=10)
            client = AIClient(cfg)
            assert client.base_url == f"http://127.0.0.1:{port}/v1"
            assert client.api_key == ""  # cloud key never sent locally
            assert "Stub polished" in client.polish("um test")
        finally:
            srv.stop("test")

    def test_idle_stop(self, tmp_path, cache):
        place_model()
        srv, cfg, port = make_server({"local_idle_stop_s": 30}, tmp_path)
        try:
            srv.ensure_ready(timeout=10)
            assert srv.maybe_idle_stop(now=time.monotonic() + 1) is False
            # idle clock starts at spawn; fast-forward past the policy
            assert srv.maybe_idle_stop(now=time.monotonic() + 61) is True
            assert srv.status()["running"] is False
        finally:
            srv.stop("test")

    def test_dying_server_detected_and_restarted(self, tmp_path, cache):
        place_model()
        srv, cfg, port = make_server({}, tmp_path)
        try:
            srv.ensure_ready(timeout=10)
            pid = srv.status()["pid"]
            import os
            os.kill(pid, 9)
            deadline = time.monotonic() + 5
            while srv.status()["running"] and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not srv.status()["running"]
            srv.ensure_ready(timeout=10)  # restarts cleanly
            assert srv.status()["running"]
        finally:
            srv.stop("test")

    def test_apply_settings_off_stops_server(self, tmp_path, cache):
        place_model()
        srv, cfg, port = make_server({}, tmp_path)
        try:
            srv.ensure_ready(timeout=10)
            cfg["ai"]["local_polish"] = False
            out = srv.apply_settings()
            assert out == {"stopped": True}
            assert srv.status()["running"] is False
        finally:
            srv.stop("test")

    def test_apply_settings_model_change_respawns(self, tmp_path, cache):
        place_model()
        srv, cfg, port = make_server({}, tmp_path)
        try:
            srv.ensure_ready(timeout=10)
            cfg["ai"]["local_model"] = "other-model"
            out = srv.apply_settings()
            assert out == {"respawn_pending": True}
            assert srv.status()["running"] is False
        finally:
            srv.stop("test")

    def test_status_for_config_never_spawns(self, cache):
        place_model()
        cfg = {"ai": {"enabled": True, "local_polish": True,
                      "local_model": model_catalog.CHAT_DEFAULT_MODEL,
                      "local_port": L.DEFAULT_PORT}}
        st = L.status_for_config(cfg)
        assert st["toggle"] is True and st["running"] is False
        assert st["model_downloaded"] is True

    def test_resolve_server_binary_path_fallback(self, cache, monkeypatch):
        import shutil as shutil_mod
        place_runtime()
        assert L.resolve_server_binary() == model_catalog.llama_server_path()
        fake = Path("/usr/bin/llama-server")
        monkeypatch.setattr(shutil_mod, "which", lambda n: str(fake))
        monkeypatch.setattr(
            model_catalog, "llama_runtime_downloaded", lambda: False)
        assert L.resolve_server_binary() == fake
        monkeypatch.setattr(shutil_mod, "which", lambda n: None)
        with pytest.raises(L.LocalServerError, match="not downloaded"):
            L.resolve_server_binary()

    def test_idle_policy_clamps(self, tmp_path):
        srv, _, _ = make_server({"local_idle_stop_s": 5}, tmp_path)
        assert srv.idle_policy_s() == 30  # below-range clamps up
        srv2, _, _ = make_server({"local_idle_stop_s": 0}, tmp_path)
        assert srv2.idle_policy_s() == 0  # 0 = keep running


# -- config registry ---------------------------------------------------------


class TestConfigKeys:
    def test_defaults(self):
        ai = DEFAULTS["ai"]
        assert ai["local_polish"] is False
        assert ai["local_model"] == "qwen3-4b-instruct-2507-q4_k_m"
        assert ai["local_port"] == 8931
        assert ai["local_idle_stop_s"] == 180

    def test_coercion(self):
        assert coerce_setting("ai", "local_model", "nope")[0] is False
        ok, v = coerce_setting(
            "ai", "local_model", model_catalog.CHAT_DEFAULT_MODEL)
        assert ok and v == model_catalog.CHAT_DEFAULT_MODEL
        assert coerce_setting("ai", "local_polish", "yes")[0] is False
        assert coerce_setting("ai", "local_port", 80)[0] is False
        assert coerce_setting("ai", "local_port", 70000)[0] is False
        ok, v = coerce_setting("ai", "local_port", 9000)
        assert ok and v == 9000
        assert coerce_setting("ai", "local_idle_stop_s", 29)[0] is False
        assert coerce_setting("ai", "local_idle_stop_s", True)[0] is False
        ok, v = coerce_setting("ai", "local_idle_stop_s", 0)
        assert ok and v == 0

    def test_keys_are_socket_settable_not_restart(self):
        from fluidvoice.config import (
            _SAVE_WHITELIST,
            ALLOWED_SETTINGS,
            RESTART_REQUIRED,
        )
        for key in ("local_polish", "local_model", "local_port",
                    "local_idle_stop_s"):
            assert key in _SAVE_WHITELIST.get("ai", [])
            assert key in ALLOWED_SETTINGS.get("ai", set())
            assert f"ai.{key}" not in RESTART_REQUIRED


# -- daemon + socket wiring ---------------------------------------------------


class FakeLocalAI:
    """Stands in for daemon._local_ai: records calls, no process."""

    def __init__(self, fail=None):
        self.fail = fail
        self.ensures = 0
        self.stops = 0
        self.applies = 0
        self._running = False

    def ensure_ready(self, timeout=None):
        self.ensures += 1
        if self.fail:
            raise L.LocalServerError(self.fail)
        self._running = True
        return {"base_url": "http://127.0.0.1:8931/v1", "model": "m",
                "api_key": ""}

    def stop(self, reason=""):
        self.stops += 1
        self._running = False

    def apply_settings(self):
        self.applies += 1
        return {}

    def status(self):
        return {"enabled": self._running, "running": self._running,
                "toggle": False}


class TestDaemonWiring:
    def make(self, cfg, monkeypatch, tmp_path, fail=None):
        import fluidvoice.config as config_mod
        monkeypatch.setattr(config_mod, "save_config",
                            lambda c, path=None: tmp_path / "c.toml")
        from fluidvoice import daemon as dm
        from tests.test_daemon import StubRecorder
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: None,
                      use_hotkey=False, use_sounds=False)
        d._local_ai = FakeLocalAI(fail)
        return d

    def base_cfg(self):
        import copy
        cfg = copy.deepcopy(DEFAULTS)
        cfg["ai"]["enabled"] = True
        cfg["ai"]["local_polish"] = True
        return cfg

    def test_process_ensures_server(self, monkeypatch, tmp_path, quiet_ui):
        d = self.make(self.base_cfg(), monkeypatch, tmp_path)
        d._ensure_local_ai("dictate")
        assert d._local_ai.ensures == 1
        d._ensure_local_ai("command")
        assert d._local_ai.ensures == 2

    def test_process_skips_when_off(self, monkeypatch, tmp_path, quiet_ui):
        cfg = self.base_cfg()
        cfg["ai"]["local_polish"] = False
        d = self.make(cfg, monkeypatch, tmp_path)
        d._ensure_local_ai("dictate")
        assert d._local_ai.ensures == 0

    def test_ensure_failure_never_raises(self, monkeypatch, tmp_path,
                                         quiet_ui):
        d = self.make(self.base_cfg(), monkeypatch, tmp_path,
                      fail="model not downloaded")
        d._ensure_local_ai("dictate")  # logs + notifies, no raise
        assert d._local_ai.ensures == 1

    def test_set_config_toggle_off_stops_server(self, monkeypatch, tmp_path,
                                                quiet_ui):
        d = self.make(self.base_cfg(), monkeypatch, tmp_path)
        resp = d.handle_request({"action": "set-config", "config": {
            "ai": {"local_polish": False}}})
        assert resp["ok"] and "ai.local_polish" in resp["changed"]
        assert d._local_ai.applies == 1
        assert d.cfg["ai"]["local_polish"] is False

    def test_shutdown_stops_server(self, monkeypatch, tmp_path, quiet_ui):
        d = self.make(self.base_cfg(), monkeypatch, tmp_path)
        d.shutdown()
        assert d._local_ai.stops == 1

    def test_status_includes_local_ai(self, monkeypatch, tmp_path, quiet_ui):
        d = self.make(self.base_cfg(), monkeypatch, tmp_path)
        resp = d.handle_request({"action": "status"})
        assert resp["local_ai"]["running"] is False

    def test_local_ai_start_stop_routes(self, monkeypatch, tmp_path,
                                        quiet_ui):
        d = self.make(self.base_cfg(), monkeypatch, tmp_path)
        resp = d.handle_request({"action": "local-ai-start"})
        assert resp["ok"] and resp["base_url"].startswith("http://127.0.0.1")
        resp = d.handle_request({"action": "local-ai-stop"})
        assert resp["ok"]

    def test_local_ai_start_failure_surfaces_error(self, monkeypatch,
                                                   tmp_path, quiet_ui):
        d = self.make(self.base_cfg(), monkeypatch, tmp_path,
                      fail="model not downloaded")
        resp = d.handle_request({"action": "local-ai-start"})
        assert resp["ok"] is False and "not downloaded" in resp["error"]

    def test_model_delete_refuses_active_chat_model(self, monkeypatch,
                                                     tmp_path, quiet_ui,
                                                     cache):
        place_model()
        d = self.make(self.base_cfg(), monkeypatch, tmp_path)
        file = model_catalog.CHAT_CATALOG[
            model_catalog.CHAT_DEFAULT_MODEL]["file"]
        resp = d.handle_request({"action": "model-delete", "kind": "llama.cpp",
                                 "name": file})
        assert resp["ok"] is False and "local polish" in resp["error"]

    def test_model_delete_allows_chat_model_when_off(self, monkeypatch,
                                                     tmp_path, quiet_ui,
                                                     cache):
        place_model()
        cfg = self.base_cfg()
        cfg["ai"]["local_polish"] = False
        d = self.make(cfg, monkeypatch, tmp_path)
        file = model_catalog.CHAT_CATALOG[
            model_catalog.CHAT_DEFAULT_MODEL]["file"]
        resp = d.handle_request({"action": "model-delete", "kind": "llama.cpp",
                                 "name": file})
        assert resp["ok"] and not model_catalog.chat_model_path(
            model_catalog.CHAT_DEFAULT_MODEL).exists()

    def test_runtime_delete_refused_while_running(self, monkeypatch,
                                                  tmp_path, quiet_ui,
                                                  cache):
        place_runtime()
        d = self.make(self.base_cfg(), monkeypatch, tmp_path)
        d._local_ai._running = True
        d._local_ai.status = lambda: {"running": True}
        resp = d.handle_request({
            "action": "model-delete", "kind": "llama.cpp-runtime",
            "name": f"llama-server ({model_catalog.LLAMA_CPP_TAG})"})
        assert resp["ok"] is False and "running" in resp["error"]


# -- AIClient override --------------------------------------------------------


class TestAIClientOverride:
    def cloud_cfg(self, **over):
        ai = {"enabled": True, "base_url": "https://cloud.example/v1",
              "model": "gpt-x", "api_key": "sk-cloud", "local_polish": False}
        ai.update(over)
        return {"ai": ai}

    def test_off_keeps_cloud(self):
        c = AIClient(self.cloud_cfg())
        assert c.base_url == "https://cloud.example/v1"
        assert c.model == "gpt-x" and c.api_key == "sk-cloud"

    def test_on_overrides_endpoint_and_drops_key(self):
        c = AIClient(self.cloud_cfg(local_polish=True, local_port=9100,
                                    local_model="qwen3-4b"))
        assert c.base_url == "http://127.0.0.1:9100/v1"
        assert c.model == "qwen3-4b"
        assert c.api_key == ""
