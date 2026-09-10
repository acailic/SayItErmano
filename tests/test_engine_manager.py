"""SpeechEngineManager (P1.2): the engine lifecycle state machine tested
with fake deps - no Daemon, no audio, no GPU. The daemon-level behavior
(load-on-first-take, status wiring) stays covered by test_idle_unload.py
and test_daemon.py through the daemon's delegation."""
from __future__ import annotations

import copy
import threading
import time

import pytest

from fluidvoice.config import DEFAULTS
from fluidvoice.engine_manager import SpeechEngineManager
from fluidvoice.runtime_tasks import RuntimeTasks


class FakeBackend:
    name = "fake"

    def __init__(self):
        self.warmed = 0
        self.closed = 0

    def warmup(self):
        self.warmed += 1

    def close(self):
        self.closed += 1


class BrokenBackend(FakeBackend):
    def __init__(self, exc=None):
        super().__init__()
        self.exc = exc or RuntimeError("no model")

    def warmup(self):
        raise self.exc


def make(cfg=None, factory=None, **cb):
    """A manager with recorded callbacks and a live RuntimeTasks."""
    cfg = cfg if cfg is not None else copy.deepcopy(DEFAULTS)
    calls = {"notify": [], "unload": [], "lang": []}
    state = {"locked": False, "active": False}

    def factory_(c):
        return FakeBackend()

    mgr = SpeechEngineManager(
        cfg, RuntimeTasks(),
        backend_factory=factory or factory_,
        lock=threading.Lock(),
        log=lambda msg: None,
        notify=lambda t, b: calls["notify"].append((t, b)),
        is_locked=lambda: state["locked"],
        is_take_active=lambda: state["active"],
        on_unload=lambda: calls["unload"].append(1),
        on_language_change=lambda lang: calls["lang"].append(lang),
        **cb)
    return mgr, calls, state


def wait_until(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# lazy load / ensure_backend
# ---------------------------------------------------------------------------

class TestEnsureBackend:
    def test_lazy_load_returns_same_instance(self):
        mgr, _, _ = make()
        assert mgr.backend is None  # lazy
        b1 = mgr.ensure_backend()
        b2 = mgr.ensure_backend()
        assert b1 is b2 and mgr.backend is b1

    def test_factory_failure_raises_for_caller(self):
        mgr, _, _ = make(factory=lambda c: (_ for _ in ()).throw(
            RuntimeError("no model")))
        with pytest.raises(RuntimeError):
            mgr.ensure_backend()
        assert mgr.backend is None  # no partial state


# ---------------------------------------------------------------------------
# select_model / warmup_model
# ---------------------------------------------------------------------------

class TestSelectModel:
    def test_unknown_model_rejected_without_claiming_warmup(self):
        mgr, _, _ = make()
        out = mgr.select_model("gpt-5")
        assert out["ok"] is False and "unknown model" in out["error"]
        assert mgr.warmup["running"] is False

    def test_select_spawns_warmup_and_hot_swaps(self, tmp_path, monkeypatch):
        import fluidvoice.backends as backends_mod
        made = []
        gate = threading.Event()

        class Swappable(FakeBackend):
            name = "faster-whisper"

            def __init__(self, c):
                super().__init__()
                made.append(c["model"]["name"])

            def warmup(self):
                gate.wait(timeout=5.0)  # hold the load for the assert
                super().warmup()

        monkeypatch.setattr(backends_mod, "load_backend",
                            lambda c: Swappable(c))
        import fluidvoice.config as config_mod
        monkeypatch.setattr(config_mod, "save_config",
                            lambda c, path=None: tmp_path / "c.toml")
        mgr, _, _ = make()
        out = mgr.select_model("turbo")
        assert out == {"ok": True, "model": "large-v3-turbo"}
        assert mgr.warmup["running"] is True  # in flight while warming
        # a second select while the download runs is refused
        assert mgr.select_model("base")["ok"] is False
        gate.set()
        assert wait_until(lambda: not mgr.warmup["running"])
        assert made == ["large-v3-turbo"]
        assert mgr.cfg["model"]["name"] == "large-v3-turbo"
        assert mgr.backend.name == "faster-whisper"

    def test_warmup_failure_rolls_back_and_reports(self, tmp_path,
                                                   monkeypatch):
        import fluidvoice.backends as backends_mod
        monkeypatch.setattr(backends_mod, "load_backend",
                            lambda c: BrokenBackend())
        import fluidvoice.config as config_mod
        monkeypatch.setattr(config_mod, "save_config",
                            lambda c, path=None: tmp_path / "c.toml")
        mgr, _, _ = make()
        mgr.cfg["model"]["name"] = "small"
        keep = mgr.backend = FakeBackend()
        assert mgr.select_model("turbo")["ok"] is True
        assert wait_until(lambda: not mgr.warmup["running"])
        assert mgr.warmup["error"]  # surfaced to the UI
        assert mgr.cfg["model"]["name"] == "small"  # rolled back
        assert mgr.backend is keep  # running backend untouched

    def test_second_select_refused_while_running(self):
        mgr, _, _ = make()
        # a load is already in flight (claimed by select/warmup_model)
        mgr.warmup = {"running": True, "error": None, "model": "x"}
        out = mgr.select_model("turbo")
        assert out["ok"] is False and "already running" in out["error"]
        assert mgr.warmup["model"] == "x"  # slot not re-claimed


# ---------------------------------------------------------------------------
# engine reload via set-config
# ---------------------------------------------------------------------------

class TestEngineReload:
    def test_start_engine_reload_claims_slot_and_spawns(self, monkeypatch):
        reloaded = []
        mgr, _, _ = make()
        monkeypatch.setattr(mgr, "reload_backend",
                            lambda: reloaded.append(1))
        assert mgr.start_engine_reload() is True
        assert mgr.warmup["running"] is True
        assert wait_until(lambda: reloaded == [1])
        # while running, a second attempt is refused
        mgr.warmup = {"running": True, "error": None, "model": "x"}
        assert mgr.start_engine_reload() is False


# ---------------------------------------------------------------------------
# idle unload policy
# ---------------------------------------------------------------------------

class TestIdleUnload:
    def test_threshold_parsing(self):
        mgr, _, _ = make()
        assert mgr.idle_threshold() == 0  # DEFAULTS: off
        mgr.cfg["model"]["idle_unload_s"] = 90
        assert mgr.idle_threshold() == 90
        mgr.cfg["model"]["idle_unload_s"] = "garbage"
        assert mgr.idle_threshold() == 0

    def test_unload_at_threshold_closes_and_notifies(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["model"]["idle_unload_s"] = 60
        mgr, calls, _ = make(cfg=cfg)
        mgr.backend = b = FakeBackend()
        base = mgr.last_activity
        mgr.maybe_idle_unload(now=base + 59)
        assert mgr.backend is b  # below: untouched
        mgr.maybe_idle_unload(now=base + 60)
        assert mgr.backend is None
        assert mgr.idle_unloaded_at == base + 60
        assert b.closed == 1
        assert calls["unload"] == [1]  # tray refresh requested

    def test_never_unloads_while_take_active_or_warming(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["model"]["idle_unload_s"] = 60
        mgr, _, state = make(cfg=cfg)
        mgr.backend = FakeBackend()
        base = mgr.last_activity
        state["active"] = True
        mgr.maybe_idle_unload(now=base + 10_000)
        assert mgr.backend is not None
        state["active"] = False
        mgr.warmup = {"running": True, "error": None, "model": "x"}
        mgr.maybe_idle_unload(now=base + 10_000)
        assert mgr.backend is not None
        mgr.warmup = {"running": False, "error": None, "model": None}

    def test_no_unload_while_eager_warm_thread_alive(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["model"]["idle_unload_s"] = 60
        mgr, _, _ = make(cfg=cfg)
        mgr.backend = FakeBackend()
        warm = threading.Thread(target=lambda: time.sleep(0.4), daemon=True)
        warm.start()
        mgr.start_warm_thread = warm
        try:
            mgr.maybe_idle_unload(now=mgr.last_activity + 10_000)
            assert mgr.backend is not None
        finally:
            warm.join()

    def test_watcher_lifecycle(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["model"]["idle_unload_s"] = 60
        mgr, _, _ = make(cfg=cfg)
        mgr.start_idle_watch()
        watcher = mgr.idle_thread
        assert watcher is not None and watcher.is_alive()
        assert watcher.name == "fluidvoice-idle-unload"
        mgr.stop_idle_watch()
        watcher.join(timeout=2)
        assert not watcher.is_alive()

    def test_policy_off_starts_no_watcher(self):
        mgr, _, _ = make()  # idle_unload_s = 0 in DEFAULTS
        mgr.start_idle_watch()
        assert mgr.idle_thread is None

    def test_apply_setting_restarts_watcher_and_touches_activity(self):
        cfg = copy.deepcopy(DEFAULTS)
        mgr, _, _ = make(cfg=cfg)
        mgr.start_idle_watch()
        assert mgr.idle_thread is None  # off
        mgr.cfg["model"]["idle_unload_s"] = 60
        mgr.apply_idle_unload_setting()
        assert mgr.idle_thread is not None
        mgr.cfg["model"]["idle_unload_s"] = 0
        mgr.apply_idle_unload_setting()
        assert mgr.idle_thread is None


# ---------------------------------------------------------------------------
# language cycle
# ---------------------------------------------------------------------------

class TestLanguageCycle:
    def _cfg(self, cycle=("auto", "en", "sl")):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["language_cycle"] = list(cycle)
        return cfg

    def test_engage_advance_wrap(self):
        mgr, calls, _ = make(cfg=self._cfg())
        assert mgr.cycle_runtime() is None  # not engaged
        seq = [mgr.cycle_language()["language"] for _ in range(4)]
        assert seq == ["auto", "en", "sl", "auto"]  # wraps to 0
        assert mgr.cycle_index == 0
        assert calls["lang"] == seq  # every press announced

    def test_empty_cycle_errors_without_engaging(self):
        mgr, calls, _ = make(cfg=self._cfg(()))
        out = mgr.cycle_language()
        assert out == {"ok": False, "error": "empty cycle"}
        assert mgr.cycle_index is None
        assert calls["notify"]  # the Settings hint fired

    def test_locked_refuses(self):
        mgr, _, state = make(cfg=self._cfg())
        state["locked"] = True
        assert mgr.cycle_language() == {"ok": False, "error": "locked"}
        assert mgr.cycle_index is None

    def test_shrunk_list_clamps(self):
        mgr, _, _ = make(cfg=self._cfg(("auto", "en", "sl")))
        for _ in range(3):
            mgr.cycle_language()
        mgr.cfg["general"]["language_cycle"] = ["de"]  # shrunk underneath
        assert mgr.cycle_runtime() == "de"

    def test_language_detail_with_backend(self):
        mgr, _, _ = make(cfg=self._cfg())
        mgr.backend = FakeBackend()
        mgr.cycle_language()  # auto
        mgr.cycle_language()  # en
        assert mgr.language_detail() == ("en", "cycle")

    def test_language_status_shape(self):
        mgr, _, _ = make(cfg=self._cfg())
        mgr.cycle_language()
        st = mgr.language_status()
        assert st["cycle"] == ["auto", "en", "sl"]
        assert st["cycle_engaged"] is True
        assert st["effective"] == "auto"
        assert st["source"] == "cycle"


# ---------------------------------------------------------------------------
# delete_model guards (deeper cache tests live in test_models_manager.py)
# ---------------------------------------------------------------------------

class TestDeleteModelGuards:
    def test_empty_name(self):
        mgr, _, _ = make()
        out = mgr.delete_model("faster-whisper", "  ")
        assert out["ok"] is False and "missing model name" in out["error"]

    def test_unknown_kind(self):
        mgr, _, _ = make()
        out = mgr.delete_model("bogus", "tiny")
        assert out["ok"] is False and "unknown model kind" in out["error"]

    def test_refuses_while_warmup_running(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        from fluidvoice import model_catalog
        decoy = model_catalog.cache_entry_path("faster-whisper", "tiny")
        decoy.mkdir(parents=True)
        mgr, _, _ = make()
        mgr.warmup = {"running": True, "error": None, "model": "x"}
        out = mgr.delete_model("faster-whisper", "tiny")
        assert out["ok"] is False and "in progress" in out["error"]
        assert decoy.exists()  # untouched
