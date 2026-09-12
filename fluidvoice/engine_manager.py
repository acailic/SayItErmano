"""SpeechEngineManager: the daemon's speech-engine lifecycle owner (P1.2).

Everything about WHICH speech backend is loaded, when it loads, warms,
reloads, unloads on idle, gets switched or deleted lives here - plus the
runtime language cycle (language resolution for takes). Extracted from
``Daemon`` (fluidvoice/daemon.py) along the plan's decomposition:

* load:      ``startup_load`` (run()-time), ``ensure_backend`` (lazy,
             serialized by ``backend_load_lock``)
* warm:      eager startup warmup thread (``startup_load``), the
             ``warmup`` status dict every UI reads
* reload:    ``reload_backend`` after engine-option changes,
             ``reload_backend_bg`` at take start after an idle unload
* idle-unload: ``maybe_idle_unload`` + the ``idle-unload`` watcher thread
             (model.idle_unload_s; 0 = off, byte-identical to the
             pre-policy behavior)
* select/delete: ``select_model`` (download + verify + hot-swap, with
             rollback) and ``delete_model`` (cache pruning with
             active-model/in-flight-load refusal)
* language resolution: the runtime language cycle
             (hotkey.language_key stepping general.language_cycle) and
             ``language_detail`` - the effective (language, source) pair
             every decode, preview and status read goes through.

Explicit dependencies, no singletons, no daemon import: the cfg dict
(read live, exactly like the daemon did), the shared daemon state
``lock``, the daemon's ``RuntimeTasks`` (named timers/threads), a
``backend_factory``, and callbacks OUT (``notify``, ``is_locked``,
``is_take_active``, ``on_unload``, ``on_language_change``). The daemon
constructs this in ``__init__`` and stays the control router.
"""
from __future__ import annotations

import gc
import threading
import time
from typing import Any, Callable

from . import backends, paths
from .runtime_tasks import RuntimeTasks

__all__ = ["SpeechEngineManager"]

Logger = Callable[[str], None]
Notifier = Callable[[str, str], None]


class SpeechEngineManager:
    """Owns the loaded backend, its lifecycle and the language cycle."""

    def __init__(self, cfg: dict, tasks: RuntimeTasks, *,
                 backend_factory: Callable[[dict], Any],
                 lock: threading.Lock,
                 log: Logger,
                 notify: Notifier | None = None,
                 is_locked: Callable[[], bool] = lambda: False,
                 is_take_active: Callable[[], bool] = lambda: False,
                 on_unload: Callable[[], None] = lambda: None,
                 on_language_change: Callable[[str], None] = lambda: None):
        self.cfg = cfg
        self._tasks = tasks
        self._backend_factory = backend_factory
        self._lock = lock
        self._log = log
        self._notify = notify or (lambda title, body: None)
        self._is_locked = is_locked
        self._is_take_active = is_take_active
        self._on_unload = on_unload
        self._on_language_change = on_language_change
        self.backend: Any = None  # lazy: loads model on first use
        self.warmup: dict = {"running": False, "error": None, "model": None}
        self._warmup_lock = threading.Lock()
        self._backend_load_lock = threading.Lock()  # serializes ensure_backend
        # -- idle model unload (model.idle_unload_s; 0 = off) --------------
        self.last_activity = time.monotonic()  # last take end / warmup
        self.idle_unloaded_at: float | None = None
        self.idle_thread: threading.Thread | None = None  # watcher when on
        self._idle_stop = threading.Event()
        self.start_warm_thread: threading.Thread | None = None  # run()'s warmup
        # language cycle: RUNTIME daemon state (hotkey.language_key steps
        # general.language_cycle); None = not engaged, never persisted
        self.cycle_index: int | None = None

    # -- startup / lazy load --------------------------------------------------

    def startup_load(self) -> None:
        """The run()-time load: best-effort immediate backend load plus the
        eager warmup thread (so live preview works from the first take).
        A failure here only logs - first use retries via ensure_backend."""
        try:
            self.backend = self._backend_factory(self.cfg)
        except Exception as e:  # noqa: BLE001 - retried on first use
            self._log(f"WARN speech backend not ready yet ({e}); "
                      "will retry on first use")
            self.backend = None
        else:
            if not self.cfg["model"].get("eager_warmup", True):
                pass  # warmup disabled (e.g. test isolation on small GPUs)
            else:
                # Load the model in the background so the live preview works
                # from the very first dictation (lazy otherwise). NOTE: the
                # snapshot lives INSIDE _eager_warm (its frame dies with the
                # thread) - a run()-level local would pin the backend for
                # the daemon's whole lifetime and defeat idle unload.
                t = self._tasks.prepare("warmup", self._eager_warm)
                self.start_warm_thread = t
                if t is not None:
                    t.start()

    def _eager_warm(self) -> None:
        ref = self.backend
        if ref is None:
            return  # dropped before the thread ran; nothing to do
        try:
            ref.warmup()
            self._log("speech model loaded (preview ready)")
            self.touch_activity()
        except Exception as e:  # noqa: BLE001 - preview stays lazy
            self._log(f"WARN model warmup failed: {e}")

    def ensure_backend(self):
        """Load the backend on demand (first take, take after an idle
        unload, onboarding tryout). The load lock serializes concurrent
        callers - the take-start background reload can race _process's
        synchronous load without double-loading."""
        with self._backend_load_lock:
            if self.backend is None:
                self.backend = self._backend_factory(self.cfg)
                # None-safe: test stub factories return None (load_backend
                # itself raises instead); logging .name on None crashed as
                # "transcription failed: 'NoneType' object has no attribute
                # 'name'" when a leaked watchdog stopped a take post-teardown
                if self.backend is not None:
                    self._log(f"speech backend: {self.backend.name}")
                self.idle_unloaded_at = None
                self.touch_activity()
        return self.backend

    # -- model warmup / hot-swap (native-app spec: select-model over socket) --

    def active_model_name(self) -> str:
        name = str(self.cfg["model"].get("name", "auto"))
        if name in ("", "auto"):
            return backends.resolve_model_name(self.cfg["model"]["name"])
        return backends.ALIASES.get(name.lower(), name.lower())

    def select_model(self, name: str) -> dict:
        name = backends.ALIASES.get(name.strip().lower(), name.strip().lower())
        if name not in backends.FW_MODEL_REPOS:
            return {"ok": False, "error": f"unknown model '{name}'"}
        with self._warmup_lock:
            if self.warmup["running"]:
                return {"ok": False,
                        "error": "a model download is already running"}
            self.warmup = {"running": True, "error": None, "model": name}
        self._tasks.spawn("model-switch", self.warmup_model, args=(name,))
        return {"ok": True, "model": name}

    def warmup_model(self, name: str) -> None:
        previous = self.cfg["model"].get("name", "auto")
        # picking a local model turns the remote backend OFF: remote wins
        # load_backend, so leaving remote_url set would silently keep
        # dictating over HTTP. Failure below rolls the name back but the
        # URL stays cleared (documented: the running remote instance
        # keeps serving until restart).
        if self.cfg["model"].get("remote_url"):
            self.cfg["model"]["remote_url"] = ""
        try:
            cfg = dict(self.cfg)
            cfg["model"] = dict(self.cfg["model"], name=name)
            backend = backends.load_backend(cfg)
            backend.warmup()
            # Persist the choice only after the model is verified usable.
            self.cfg["model"]["name"] = name
            from .config import save_config
            save_config(self.cfg)
            self.backend = backend  # hot-swap into the running daemon
            self.idle_unloaded_at = None
            self.touch_activity()
            self.warmup = {"running": False, "error": None, "model": name}
            self._log(f"model switched to {name} (hot-swapped)")
        except Exception as e:  # noqa: BLE001 - surfaced in the UI
            self.cfg["model"]["name"] = previous  # rollback, keep usable
            self.warmup = {"running": False, "error": str(e)[:300],
                           "model": name}
            self._log(f"model switch to {name} failed: {e}")

    def reload_backend(self) -> None:
        """Rebuild the loaded backend after engine-option changes. Config
        stays saved even on failure - the running backend keeps its last
        working state until the next try."""
        model = self.active_model_name()
        try:
            backend = backends.load_backend(self.cfg)
            backend.warmup()
            self.backend = backend
            self.idle_unloaded_at = None
            self.touch_activity()
            self.warmup = {"running": False, "error": None, "model": model}
        except Exception as e:  # noqa: BLE001 - surfaced in the UI
            self.warmup = {"running": False, "error": str(e)[:300],
                           "model": model}

    def start_engine_reload(self) -> bool:
        """set-config engine-key path: claim the warmup slot and spawn the
        background reload. False when a load is already running."""
        with self._warmup_lock:
            if self.warmup["running"]:
                return False
            self.warmup = {"running": True, "error": None,
                           "model": self.active_model_name()}
            self._tasks.spawn("engine-reload", self.reload_backend)
            return True

    def reload_backend_bg(self) -> None:
        """Take-start reload after an idle unload (or a failed startup
        load): the model loads while the user speaks; _process's
        synchronous ensure_backend remains the guaranteed - and gracefully
        failing - path."""
        try:
            self.ensure_backend()
        except Exception as e:  # noqa: BLE001 - logged, retried at stop
            self._log(f"WARN background model reload failed: {e}")

    def delete_model(self, kind: str, name: str) -> dict:
        """Remove one cached model (Settings -> Models pruning). The target
        is resolved from kind+name under the managed cache root - a client
        path is never trusted; the active model and in-flight loads are
        refused."""
        import shutil

        from . import model_catalog
        name = name.strip()
        if not name:
            return {"ok": False, "error": "missing model name"}
        try:
            target = model_catalog.cache_entry_path(kind, name)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not target.exists():
            return {"ok": False,
                    "error": f"{name} is not in the models cache"}
        root = paths.models_dir().resolve()
        t = target.resolve()
        if t == root or not t.is_relative_to(root):
            return {"ok": False,
                    "error": "refusing to delete outside the models cache"}
        active = backends.backend_model_key(self.backend) \
            or backends.config_model_key(self.cfg)
        if name == active:
            return {"ok": False,
                    "error": f"{name} is the active model (switch models first)"}
        if self.warmup.get("running"):
            return {"ok": False,
                    "error": "a model load is in progress - try again once "
                             "it finishes"}
        freed = (model_catalog._dir_size(t) if t.is_dir()
                 else t.stat().st_size)
        if t.is_dir():
            shutil.rmtree(t)
        else:
            t.unlink()
        self._log(f"deleted cached model {kind} {name} "
                  f"(freed {model_catalog.human_bytes(freed)}): {t}")
        return {"ok": True, "path": str(t), "bytes": freed}

    # -- idle model unload (model.idle_unload_s) ------------------------------

    def idle_threshold(self) -> int:
        """Current policy in seconds; 0 = never unload."""
        try:
            return int((self.cfg.get("model", {}) or {})
                       .get("idle_unload_s", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def touch_activity(self) -> None:
        """Record dictation activity (resets the idle clock). Called from
        take start/stop, transcription end, successful warmups and policy
        changes - deliberately NOT from status/preview/history reads."""
        self.last_activity = time.monotonic()

    def maybe_idle_unload(self, now: float | None = None) -> None:
        """Drop the loaded backend once no dictation activity happened for
        model.idle_unload_s seconds. `now` is injectable for tests. Never
        fires while recording/busy/warming up; the drop itself happens
        outside the shared state lock (backend destructors can take
        ~100 ms), but inside backend_load_lock so a load can never land
        mid-drop."""
        if now is None:
            now = time.monotonic()
        t = self.idle_threshold()
        if t <= 0:
            return
        with self._backend_load_lock:
            with self._lock:
                if self.backend is None or self._is_take_active():
                    return
                if self.warmup.get("running"):
                    return  # a model switch/download is in flight
                warm = self.start_warm_thread
                if warm is not None and warm.is_alive():
                    return  # eager startup warmup still running
                idle_s = now - self.last_activity
                # FP guard: `now` in these paths is `last_activity + t`
                # computed in float — with large monotonic bases that sum
                # can round a hair UNDER t (observed on CI: 59.99999999999
                # vs 60), refusing an exactly-threshold unload. An idle of
                # at-least-t must fire; only a genuinely-shorter one waits.
                if idle_s < t - 1e-9:
                    return
                backend = self.backend
                self.backend = None
                self.idle_unloaded_at = now
            try:
                backend.close()
            except Exception:  # noqa: BLE001 - teardown is best-effort
                pass
            del backend  # release before gc so cycles die here too
            gc.collect()
        self._log(f"model idle {int(idle_s // 60)}m >= {t}s - unloaded "
                  "(reloads on the next dictation)")
        self._on_unload()

    def _idle_watch_loop(self) -> None:
        while not self._idle_stop.wait(
                max(5.0, min(60.0, self.idle_threshold() / 4.0))):
            try:
                self.maybe_idle_unload()
            except Exception as e:  # noqa: BLE001 - the watcher must survive
                self._log(f"WARN idle-unload check failed: {e}")

    def start_idle_watch(self) -> None:
        """Start the watcher thread iff the policy is on. With
        idle_unload_s = 0 nothing runs - behavior is identical to the
        pre-policy daemon."""
        if self.idle_threshold() <= 0:
            self.idle_thread = None
            return
        if self.idle_thread is not None and self.idle_thread.is_alive():
            return
        self._idle_stop = threading.Event()
        t = self._tasks.prepare("idle-unload", self._idle_watch_loop)
        self.idle_thread = t
        if t is not None:
            t.start()

    def stop_idle_watch(self) -> None:
        self._idle_stop.set()
        self._tasks.join("idle-unload", timeout=2)

    def apply_idle_unload_setting(self) -> None:
        """model.idle_unload_s flipped live: restart (or stop) the watcher.
        Setting the policy counts as activity, so a just-enabled window
        never fires immediately."""
        self.stop_idle_watch()
        self.touch_activity()
        self.start_idle_watch()

    # -- language cycle (hotkey.language_key / general.language_cycle) ------

    def cycle_list(self) -> list[str]:
        """The ordered cycle list, read LIVE on every use so Settings edits
        apply without a restart. Empty list = the feature is off even when
        the key is bound."""
        return list(((self.cfg.get("general", {}) or {})
                     .get("language_cycle")) or []) \
            if isinstance(self.cfg, dict) else []

    def cycle_runtime(self) -> str | None:
        """The runtime cycle override for the next take: the cycle entry at
        the engaged index (clamped modulo a shrunk list), or None when the
        cycle is not engaged or the list was emptied underneath it."""
        cycle = self.cycle_list()
        if self.cycle_index is None or not cycle:
            return None
        return cycle[self.cycle_index % len(cycle)]

    def language_detail(self) -> tuple[str, str]:
        """(effective language, source) for status/doctor/tooltip. The lazy
        backend may be None; language_detail resolves via the config key."""
        return backends.language_detail(self.cfg, self.backend,
                                        self.cycle_runtime() or "")

    def cycle_language(self) -> dict:
        """Language-cycle hotkey handler: engage at index 0 on the first
        press, advance (with wrap-around) afterwards. The override is pure
        runtime state - never written to config. A mid-take press is
        announced immediately via on_language_change; the final decode
        re-resolves at stop time (the preview engine keeps the language it
        captured at take start)."""
        if self._is_locked():
            return {"ok": False, "error": "locked"}  # session locked
        cycle = self.cycle_list()
        if not cycle:
            self._log("WARN language cycle: general.language_cycle is empty")
            self._notify("SayItErmano",
                         "Language cycle is empty — set "
                         "general.language_cycle in Settings")
            return {"ok": False, "error": "empty cycle"}
        self.cycle_index = 0 if self.cycle_index is None \
            else (self.cycle_index + 1) % len(cycle)
        lang, source = self.language_detail()
        self._log(f"language cycle -> {lang} ({source})")
        self._on_language_change(lang)
        return {"ok": True, "language": lang, "source": source}

    def language_status(self) -> dict:
        """The additive "language" status block (CLI/GTK client/doctor)."""
        lang, source = self.language_detail()
        return {"effective": lang, "source": source,
                "cycle": self.cycle_list(),
                "cycle_engaged": self.cycle_runtime() is not None,
                "whitelist": list(((self.cfg.get("general", {}) or {})
                                   .get("language_whitelist")) or [])}
