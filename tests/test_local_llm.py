"""Local polish (#21): managed llama.cpp server lifecycle, chat-model
catalog/download, config keys, and the pipeline/daemon seams.

Process tests spawn the REAL fake binary (tests/fake_llama_server.py,
copied into tmp with +x) so spawn/health/terminate/idle paths exercise
actual subprocess machinery on loopback only. Logic tests use injected
binary finders / health probes and never spawn anything.
"""
from __future__ import annotations

import copy
import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from fluidvoice import model_catalog, model_download
from fluidvoice.ai.client import AIError
from fluidvoice.config import DEFAULTS, coerce_setting
from fluidvoice.local_llm import LocalPolishError, LocalPolishManager
from fluidvoice.runtime_tasks import RuntimeTasks

FAKE_SERVER = Path(__file__).parent / "fake_llama_server.py"
MODEL = model_catalog.CHAT_MODEL_DEFAULT


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def put_model(name: str = MODEL, size: int = 1024) -> Path:
    """Create the chat GGUF in the managed cache (paths read the test's
    XDG override lazily)."""
    p = model_catalog.chat_model_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\0" * size)
    return p


def make_fake_binary(tmp_path: Path, mode: str = "ok") -> Path:
    """Copy the fake llama-server into tmp with +x (the manager spawns it
    through ai.local_server_binary; FAKE_LLAMA_MODE comes from the env)."""
    dst = tmp_path / f"llama-server-{mode}"
    shutil.copy(FAKE_SERVER, dst)
    dst.chmod(0o755)
    return dst


def make_manager(cfg, *, log=None, **kw) -> LocalPolishManager:
    tasks = RuntimeTasks()
    m = LocalPolishManager(cfg, tasks,
                           log=log if log is not None else (lambda msg: None),
                           **kw)
    m._tasks_holder = tasks  # keep alive for shutdown joins in tests
    return m


def local_cfg(**ai_over) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    cfg["ai"].update({"local_polish": True, "local_idle_s": 300,
                      "enabled": False, "base_url": "http://cloud/v1",
                      "model": "cloud-model", "api_key": "sekret"})
    cfg["ai"].update(ai_over)
    return cfg


def read_log(logf: Path) -> list[dict]:
    if not logf.exists():
        return []
    return [json.loads(ln) for ln in logf.read_text().splitlines()
            if ln.strip()]


# ---------------------------------------------------------------------------
# chat model catalog + download
# ---------------------------------------------------------------------------

class TestChatCatalog:
    def test_catalog_has_pinned_qwen3_entry(self):
        entry = model_catalog.CHAT_GGUF_CATALOG[MODEL]
        assert entry["url"].startswith("https://huggingface.co/")
        assert len(entry["sha256"]) == 64
        assert "2.5 GB" in entry["size"]
        assert model_catalog.CHAT_MODEL_DEFAULT == MODEL
        # the config default must track the catalog (hand-synced literal)
        assert DEFAULTS["ai"]["local_model"] == MODEL

    def test_paths_under_managed_cache(self, cache):
        p = model_catalog.chat_model_path(MODEL)
        assert p == (model_catalog.paths.models_dir()
                     / model_catalog.CHAT_GGUF_DIR_NAME / MODEL)

    def test_downloaded_probe(self, cache):
        assert not model_catalog.chat_model_downloaded(MODEL)
        put_model()
        assert model_catalog.chat_model_downloaded(MODEL)

    def test_cached_models_lists_chat_kind(self, cache):
        put_model(size=4096)
        entries = [e for e in model_catalog.cached_models()
                   if e["kind"] == "chat"]
        assert len(entries) == 1
        assert entries[0]["name"] == MODEL
        assert entries[0]["bytes"] == 4096

    def test_cache_entry_path_chat(self, cache):
        p = model_catalog.cache_entry_path("chat", MODEL)
        assert p == model_catalog.chat_model_path(MODEL)
        with pytest.raises(ValueError, match="unknown chat model"):
            model_catalog.cache_entry_path("chat", "not-a-model.gguf")


class TestChatDownload:
    def _fake_urlopen(self, monkeypatch, chunks, length):
        from tests.test_model_download import FakeResp

        def fake(req, timeout=None):
            return FakeResp(chunks, length=length)
        monkeypatch.setattr(model_download.urllib.request, "urlopen", fake)

    def test_happy_path_verifies_sha(self, cache, monkeypatch):
        import hashlib
        payload = b"qwen" * 128
        self._fake_urlopen(monkeypatch, [payload], len(payload))
        # pin the digest to the payload so the verify passes
        entry = model_catalog.CHAT_GGUF_CATALOG[MODEL]
        monkeypatch.setitem(entry, "sha256",
                            hashlib.sha256(payload).hexdigest())
        events = []
        dest = model_download.download_chat_model(
            MODEL, progress=lambda b, t: events.append((b, t)))
        assert dest.read_bytes() == payload
        assert events[-1] == (len(payload), len(payload))

    def test_sha_mismatch_deletes_and_raises(self, cache, monkeypatch):
        payload = b"corrupted weights"
        self._fake_urlopen(monkeypatch, [payload], len(payload))
        with pytest.raises(OSError, match="checksum mismatch"):
            model_download.download_chat_model(MODEL)
        assert not model_catalog.chat_model_path(MODEL).exists()

    def test_unknown_name_rejected(self, cache):
        with pytest.raises(ValueError, match="unknown chat model"):
            model_download.download_chat_model("nope.gguf")

    def test_existing_file_is_noop(self, cache, monkeypatch):
        put_model()
        def boom(req, timeout=None):  # pragma: no cover - must not run
            raise AssertionError("no download expected")
        monkeypatch.setattr(model_download.urllib.request, "urlopen", boom)
        assert model_download.download_chat_model(MODEL) == \
            model_catalog.chat_model_path(MODEL)

    def test_chat_part_visible_to_observers(self, cache):
        part = model_catalog.chat_model_path(MODEL).with_name(
            MODEL + ".part")
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(b"x" * 1000)
        obs = [d for d in model_download.observe_downloads()
               if d.kind == "chat"]
        assert obs and obs[0].label == MODEL
        assert obs[0].total_bytes  # estimated from the catalog size note


class TestLocalConfigKeys:
    def test_defaults(self):
        assert DEFAULTS["ai"]["local_polish"] is False
        assert DEFAULTS["ai"]["local_idle_s"] == 300
        assert DEFAULTS["ai"]["local_server_binary"] == ""

    def test_local_model_coercion(self):
        assert coerce_setting("ai", "local_model", MODEL) == (True, MODEL)
        assert coerce_setting("ai", "local_model", "other.gguf")[0] is False

    def test_local_idle_bounds(self):
        ok, _ = coerce_setting("ai", "local_idle_s", 30)
        assert ok
        assert coerce_setting("ai", "local_idle_s", 0)[0]
        for bad in (10, 86401, True, "300"):
            assert coerce_setting("ai", "local_idle_s", bad)[0] is False

    def test_local_binary_coercion(self):
        assert coerce_setting("ai", "local_server_binary", "") == (True, "")
        assert coerce_setting("ai", "local_server_binary",
                              "/usr/local/bin/llama-server") \
            == (True, "/usr/local/bin/llama-server")
        for bad in ("llama-server", "not a path", 5, None):
            assert coerce_setting("ai", "local_server_binary", bad)[0] is False

    def test_all_keys_settable_and_saved(self):
        for key in ("local_polish", "local_model", "local_idle_s",
                    "local_server_binary"):
            spec = ("ai", key)
            assert coerce_setting(*spec, DEFAULTS["ai"][key])[0], key


# ---------------------------------------------------------------------------
# manager logic (no subprocesses)
# ---------------------------------------------------------------------------

class TestManagerLogic:
    def test_zero_footprint_when_disabled(self):
        cfg = local_cfg(local_polish=False)
        m = make_manager(cfg)
        from fluidvoice.ai.client import AIClient
        client = m.polish_client(cfg)
        assert isinstance(client, AIClient)
        assert client.base_url == "http://cloud/v1"  # untouched
        st = m.status()
        assert st["enabled"] is False and st["running"] is False
        assert m._tasks_holder.state("local-polish-idle") is None

    def test_missing_model_error_is_actionable(self, cache):
        cfg = local_cfg()
        m = make_manager(cfg)
        with pytest.raises(AIError, match="not downloaded"):
            m.polish_client(cfg)
        assert "not downloaded" in (m.status()["last_error"] or "")

    def test_missing_binary_error_is_actionable(self, cache):
        put_model()
        cfg = local_cfg()
        m = make_manager(cfg, binary_finder=lambda c: None)
        with pytest.raises(AIError, match="llama-server"):
            m.polish_client(cfg)

    def test_readiness_reflects_probes(self, cache):
        cfg = local_cfg()
        m = make_manager(cfg, binary_finder=lambda c: "/usr/bin/llama-server")
        r = m.readiness()
        assert r == {"enabled": True, "model": MODEL,
                     "model_downloaded": False,
                     "binary": "/usr/bin/llama-server"}
        put_model()
        assert m.readiness()["model_downloaded"] is True

    def test_unknown_saved_model_falls_back_to_default(self, cache):
        cfg = local_cfg(local_model="not-in-catalog.gguf")
        m = make_manager(cfg, binary_finder=lambda c: None)
        assert m.readiness()["model"] == MODEL

    def test_idle_threshold_reads_cfg(self):
        assert make_manager(local_cfg()).idle_threshold() == 300
        assert make_manager(local_cfg(local_idle_s=0)).idle_threshold() == 0


# ---------------------------------------------------------------------------
# real-subprocess lifecycle (loopback fake llama-server)
# ---------------------------------------------------------------------------

class TestManagerProcess:
    @pytest.fixture()
    def env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        put_model()
        logf = tmp_path / "llama-log.jsonl"
        monkeypatch.setenv("FAKE_LLAMA_LOG", str(logf))
        binary = make_fake_binary(tmp_path, "ok")
        cfg = local_cfg(local_server_binary=str(binary))
        return {"cfg": cfg, "logf": logf, "binary": binary,
                "tmp_path": tmp_path}

    def test_spawn_polish_and_flags(self, env):
        m = make_manager(env["cfg"])
        try:
            url = m.ensure_running()
            assert url.startswith("http://127.0.0.1:") and url.endswith("/v1")
            out = m.polish_text("um hello local polish world")
            assert out == "Hello local polish world."
            startup = read_log(env["logf"])[0]
            argv = startup["argv"]
            assert "--host" in argv and argv[argv.index("--host") + 1] \
                == "127.0.0.1"
            assert argv[argv.index("--alias") + 1] == "local-polish"
            assert argv[argv.index("-m") + 1] \
                == str(model_catalog.chat_model_path(MODEL))
            reqs = [e for e in read_log(env["logf"]) if "body" in e]
            assert reqs[0]["body"]["model"] == "local-polish"
            assert reqs[0]["authorization"] is None  # no key on loopback
        finally:
            m.shutdown()

    def test_client_patches_cfg_not_original(self, env):
        cfg = env["cfg"]
        frozen = copy.deepcopy(cfg)
        m = make_manager(cfg)
        try:
            client = m.polish_client(cfg)
            assert client.base_url.startswith("http://127.0.0.1:")
            assert client.model == "local-polish"
            assert client.api_key == ""
            assert cfg == frozen  # the user's cloud profile is untouched
        finally:
            m.shutdown()

    def test_spawn_failure_surfaces_log_hint(self, env, monkeypatch):
        monkeypatch.setenv("FAKE_LLAMA_MODE", "die")
        m = make_manager(env["cfg"], startup_timeout_s=5)
        with pytest.raises(LocalPolishError, match="exited during startup"):
            m.ensure_running()
        assert m.status()["running"] is False

    def test_health_timeout_raises(self, env, monkeypatch):
        monkeypatch.setenv("FAKE_LLAMA_MODE", "hang")
        m = make_manager(env["cfg"], startup_timeout_s=1.0)
        try:
            with pytest.raises(LocalPolishError, match="not ready"):
                m.ensure_running()
        finally:
            m.shutdown()

    def test_idle_stop_and_respawn(self, env):
        m = make_manager(env["cfg"])
        m.ensure_running()
        proc1 = m._proc
        # below threshold: never
        m.maybe_idle_stop(now=m.last_polish + 100)
        assert m._proc is proc1
        # at threshold: stops (FP-guard: exactly-threshold fires)
        m.maybe_idle_stop(now=m.last_polish + 300)
        assert m._proc is None and proc1.poll() is not None
        assert m.status()["running"] is False
        # respawn works and picks a port again
        url2 = m.ensure_running()
        assert url2.startswith("http://127.0.0.1:")
        m.shutdown()

    def test_in_flight_blocks_idle_stop(self, env):
        m = make_manager(env["cfg"])
        m.ensure_running()
        proc = m._proc
        with m._lock:
            m._in_flight = 1
        m.maybe_idle_stop(now=m.last_polish + 10_000)
        assert m._proc is proc and proc.poll() is None
        with m._lock:
            m._in_flight = 0
        m.maybe_idle_stop(now=m.last_polish + 10_000)
        assert proc.poll() is not None
        m.shutdown()

    def test_stop_is_zero_footprint(self, env):
        m = make_manager(env["cfg"])
        m.ensure_running()
        proc = m._proc
        watcher = m._idle_thread
        m.stop("test")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.02)
        assert proc.poll() is not None
        if watcher is not None:
            watcher.join(timeout=5)
            assert not watcher.is_alive()
        assert m.status()["running"] is False
        assert m._tasks_holder.state("local-polish-idle") in (None, "done")

    def test_apply_setting_change_off_stops_child(self, env):
        m = make_manager(env["cfg"])
        m.ensure_running()
        proc = m._proc
        env["cfg"]["ai"]["local_polish"] = False
        m.apply_setting_change()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.02)
        assert proc.poll() is not None

    def test_disabled_never_spawns_even_if_ready(self, env):
        env["cfg"]["ai"]["local_polish"] = False
        m = make_manager(env["cfg"])
        client = m.polish_client(env["cfg"])
        assert client.base_url == "http://cloud/v1"
        assert m._proc is None


# ---------------------------------------------------------------------------
# pipeline + daemon seams
# ---------------------------------------------------------------------------

@pytest.fixture()
def cfg():
    return copy.deepcopy(DEFAULTS)


@pytest.fixture()
def quiet_ui(tmp_path, monkeypatch):
    from fluidvoice import daemon as dm
    calls = {"notify": []}

    def fake_notify(title, body="", timeout_ms=2500, enabled=True):
        if enabled:
            calls["notify"].append((title, body))

    monkeypatch.setattr(dm.ui, "notify", fake_notify)
    monkeypatch.setattr(dm.ui, "play_sound", lambda *a, **k: None)
    monkeypatch.setattr(dm.history_mod.paths, "history_file",
                        lambda: tmp_path / "test-history.jsonl")
    return calls


class SimpleClient:
    def __init__(self, polish):
        self.polish = polish


class TestPipelineSeam:
    def test_local_polish_implies_polish_enabled(self, tmp_path, cfg,
                                                 quiet_ui):
        """ai.local_polish on with ai.enabled off: the take is still
        polished through the injected factory (one-click contract)."""
        from fluidvoice import daemon as dm
        from tests.test_daemon import StubBackend, make_wav

        cfg["ai"]["enabled"] = False
        cfg["ai"]["local_polish"] = True
        seen = {}

        class FakeClient:
            def __init__(self, c):
                seen["cfg"] = c

            def polish(self, text, system_prompt=None):
                seen["text"] = text
                return f"[local] {text}"

        pipe = dm.DictationPipeline(cfg, StubBackend("raw words"),
                                    inserter=lambda t, c: "typed")
        pipe._ai_factory = lambda c: FakeClient(c)
        out = pipe.run(make_wav(tmp_path / "utt.wav"), "TestApp")
        assert out["text"] == "[local] raw words"
        assert out["ai"] is True
        assert seen["cfg"] is cfg

    def test_off_polish_still_requires_master_switch(self, tmp_path, cfg,
                                                     quiet_ui):
        from fluidvoice import daemon as dm
        from tests.test_daemon import StubBackend, make_wav

        cfg["ai"]["enabled"] = False
        cfg["ai"]["local_polish"] = False
        pipe = dm.DictationPipeline(cfg, StubBackend("raw words"),
                                    inserter=lambda t, c: "typed")
        out = pipe.run(make_wav(tmp_path / "utt.wav"), "TestApp")
        assert out["text"] == "raw words" and out["ai"] is False

    def test_local_ai_error_falls_back_to_raw(self, tmp_path, cfg, quiet_ui):
        from fluidvoice import daemon as dm
        from tests.test_daemon import StubBackend, make_wav

        cfg["ai"]["enabled"] = False
        cfg["ai"]["local_polish"] = True

        def failing_factory(_cfg):
            def polish(text, system_prompt=None):
                raise AIError("local polish model not downloaded")
            return SimpleClient(polish)

        pipe = dm.DictationPipeline(cfg, StubBackend("raw words"),
                                    inserter=lambda t, c: "typed")
        pipe._ai_factory = failing_factory
        out = pipe.run(make_wav(tmp_path / "utt.wav"), "TestApp")
        assert out["text"] == "raw words" and out["ai"] is False


class _RecordingPipeline:
    """Pipeline stub that records whether the daemon injected the local
    polish client factory (the #ai_factory seam, set as _ai_factory like
    _profile_override)."""

    instances: list = []

    def __init__(self, cfg, backend):
        self.cfg, self.backend = cfg, backend
        self._ai_factory = None
        type(self).instances.append(self)

    def run(self, wav, app_hint, mode="dictate", rewrite_context=None):
        wav.unlink(missing_ok=True)
        return {"text": "t", "mode": mode}


class TestDaemonSeam:
    def _daemon(self, cfg):
        from fluidvoice import daemon as dm
        from tests.test_daemon import StubRecorder
        _RecordingPipeline.instances = []
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: _stub_backend(),
                      pipeline_factory=_RecordingPipeline,
                      use_hotkey=False, use_sounds=False)
        return d

    def test_daemon_injects_factory_only_when_enabled(self, cfg, tmp_path):
        from tests.test_daemon import make_wav
        cfg["ai"]["local_polish"] = True
        d = self._daemon(cfg)
        d._process(make_wav(tmp_path / "utt.wav"), None)
        assert _RecordingPipeline.instances[-1]._ai_factory is not None
        d.shutdown()

        cfg2 = local_cfg(local_polish=False)
        d2 = self._daemon(cfg2)
        d2._process(make_wav(tmp_path / "utt2.wav"), None)
        assert _RecordingPipeline.instances[-1]._ai_factory is None
        d2.shutdown()

    def test_status_action_carries_local_polish_block(self, cfg):
        d = self._daemon(cfg)
        resp = d.handle_request({"action": "status"})
        lp = resp["local_polish"]
        assert lp["enabled"] is False and lp["running"] is False
        assert lp["model"] == MODEL and "policy_s" in lp
        d.shutdown()

    def test_local_polish_stop_route(self, cfg, monkeypatch):
        stopped = {}
        d = self._daemon(cfg)
        monkeypatch.setattr(
            d._local_polish, "stop",
            lambda reason="": stopped.setdefault("reason", reason))
        resp = d.handle_request({"action": "local-polish-stop"})
        assert resp["ok"] is True and stopped["reason"] == "control route"
        d.shutdown()

    def test_apply_config_ai_local_keys_pokes_manager(self, cfg, monkeypatch):
        poked = []
        d = self._daemon(cfg)
        monkeypatch.setattr(d._local_polish, "apply_setting_change",
                            lambda: poked.append(1))
        feedback = d.apply_config(["ai.local_polish"])
        assert "local polish" in feedback["applied"]
        # non-ai.local keys do not poke it
        d.apply_config(["model.idle_unload_s"])
        assert poked == [1]
        d.shutdown()

    def test_shutdown_stops_manager(self, cfg, monkeypatch):
        events = []
        d = self._daemon(cfg)
        monkeypatch.setattr(d._local_polish, "shutdown",
                            lambda: events.append("shutdown"))
        d.shutdown()
        assert events == ["shutdown"]

    def test_daemon_owns_manager_wired_to_cfg(self, cfg):
        d = self._daemon(cfg)
        assert d._local_polish.cfg is cfg
        d.shutdown()


class TestDeleteChatModelGuard:
    def test_refuses_while_local_polish_active(self, cfg, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(_fresh_tmp()))
        from fluidvoice.engine_manager import SpeechEngineManager
        from fluidvoice.runtime_tasks import RuntimeTasks
        put_model()
        cfg["ai"]["local_polish"] = True
        mgr = SpeechEngineManager(cfg, RuntimeTasks(),
                                  backend_factory=lambda c: None,
                                  lock=threading.Lock(), log=lambda m: None)
        resp = mgr.delete_model("chat", MODEL)
        assert resp["ok"] is False and "local polish" in resp["error"]
        # off: deletion works through the managed cache
        cfg["ai"]["local_polish"] = False
        resp = mgr.delete_model("chat", MODEL)
        assert resp["ok"] is True
        assert not model_catalog.chat_model_path(MODEL).exists()


def _stub_backend():
    from tests.test_daemon import StubBackend
    return StubBackend("stub text")


def _fresh_tmp() -> Path:
    import tempfile
    return Path(tempfile.mkdtemp(prefix="sayit-local-polish-"))
