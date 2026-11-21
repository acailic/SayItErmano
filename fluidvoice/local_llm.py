"""Managed llama.cpp server for local AI polish (#21, ADR-0007).

``LocalPolishManager`` owns the whole lifecycle of an ``llama-server``
child so that AI polish works offline with zero setup:

* **On demand** — the server is spawned only when a polish call is
  actually imminent (first dictation after idle/daemon start), on an
  ephemeral 127.0.0.1 port, ready-polled through ``/health``.
* **Auto-config** — polish clients are built from the user's cfg with
  ``base_url``/``model``/``api_key`` patched onto the child's endpoint
  (``polish_client``). The saved cloud profile is never rewritten: the
  override resolves per take, so turning the toggle off restores the
  cloud endpoint instantly.
* **Idles down** — after ``ai.local_idle_s`` without polish traffic
  (default 300 s; 0 = keep until the daemon exits) the child is
  terminated. A repeating watcher thread exists ONLY while the server
  runs (``RuntimeTasks``-supervised, cancelled at stop).
* **Zero footprint off** — with ``ai.local_polish`` false nothing is
  ever spawned and no watcher exists; ``polish_client`` degrades to the
  stock ``AIClient(cfg)`` path; ``apply_setting_change`` stops a running
  child the moment the toggle flips off.

This is not a daemon control interface (ADR-0001 stands): the child
serves only the chat-completions endpoint the polish client consumes,
loopback-bound, port never persisted — the same trust class as a user
running ``ollama serve``, except we own its lifecycle.

Testability: every external effect is injectable (``binary_finder``,
``health_probe``, ``startup_timeout_s``), and the real process tests
drive a script that speaks the same flag/HTTP surface as llama-server
(tests/fake_llama_server.py).
"""
from __future__ import annotations

import copy
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import model_catalog, paths
from .runtime_tasks import RuntimeTasks

__all__ = ["LocalPolishManager", "LocalPolishError", "find_llama_server"]

Logger = Callable[[str], None]
Notifier = Callable[[str, str], None]

#: llama-server model alias — the model name polish requests send. A
#: fixed alias keeps the request shape independent of the GGUF file name.
MODEL_ALIAS = model_catalog.CHAT_MODEL_ALIAS

#: context window handed to the server. Polish prompts are ~2 kB + the
#: take, so 4096 tokens is generous; llama-server would otherwise pick
#: its build default (which can be as low as 512 on older builds).
DEFAULT_CTX = 4096

#: how long /health is polled after spawn before giving up (seconds).
#: Loading a ~2.5 GB Q4 model from cold page cache on CPU takes 10-30 s;
#: 90 covers a slow disk while still failing honestly.
DEFAULT_STARTUP_TIMEOUT_S = 90.0

_LOG_MAX_BYTES = 2 * 1024 * 1024  # rotate the child log above this


class LocalPolishError(RuntimeError):
    """The managed llama.cpp server could not be used (missing model,
    missing binary, failed spawn). Actionable text, one per cause."""


def find_llama_server(cfg: dict) -> str | None:
    """Resolve the llama-server binary: the explicit
    ``ai.local_server_binary`` path when set (trusted as-is), else the
    first ``llama-server`` on PATH. None when nothing was found."""
    ai = (cfg.get("ai", {}) or {}) if isinstance(cfg, dict) else {}
    explicit = str(ai.get("local_server_binary", "") or "").strip()
    if explicit:
        return explicit
    for name in ("llama-server",):
        p = shutil.which(name)
        if p:
            return p
    return None


def _free_loopback_port() -> int:
    """A kernel-free ephemeral port on 127.0.0.1 (bind(0), read, close).

    The window between close and the child's bind is tiny and the
    failure mode is honest: llama-server exits on EADDRINUSE and the
    spawn surfaces its log tail."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _default_health_probe(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 - any transport answer = not ready
        return False


class LocalPolishManager:
    """Owns the local-polish llama.cpp child process and its idle watch.

    Constructing the manager has zero footprint (no threads, no
    processes); the watcher thread only exists between the first
    successful spawn and the matching stop."""

    def __init__(self, cfg: dict, tasks: RuntimeTasks, *,
                 log: Logger = lambda msg: None,
                 notify: Notifier | None = None,
                 binary_finder: Callable[[dict], str | None] = find_llama_server,
                 health_probe: Callable[[str], bool] = _default_health_probe,
                 startup_timeout_s: float = DEFAULT_STARTUP_TIMEOUT_S):
        self.cfg = cfg
        self._tasks = tasks
        self._log = log
        self._notify = notify or (lambda title, body: None)
        self._binary_finder = binary_finder
        self._health_probe = health_probe
        self._startup_timeout_s = startup_timeout_s
        self._lock = threading.Lock()  # guards spawn/stop/state below
        self._proc: subprocess.Popen | None = None
        self._port: int | None = None
        self._in_flight = 0          # polish calls running right now
        self.last_polish = 0.0       # monotonic; 0 = never
        self.last_error: str | None = None
        self._idle_stop = threading.Event()
        self._idle_thread: threading.Thread | None = None

    # -- readiness ---------------------------------------------------------

    def enabled(self) -> bool:
        """The toggle is on (config only — readiness is probed lazily)."""
        ai = self.cfg.get("ai", {}) or {}
        return bool(ai.get("local_polish"))

    def readiness(self) -> dict:
        """{enabled, model, model_downloaded, binary} — cheap probes for
        status/UI surfaces. Never spawns anything."""
        ai = self.cfg.get("ai", {}) or {}
        name = str(ai.get("local_model", "") or model_catalog.CHAT_MODEL_DEFAULT)
        binary = self._binary_finder(self.cfg)
        return {
            "enabled": self.enabled(),
            "model": name if name in model_catalog.CHAT_GGUF_CATALOG
                     else model_catalog.CHAT_MODEL_DEFAULT,
            "model_downloaded": model_catalog.chat_model_downloaded(name),
            "binary": binary,
        }

    def _require_ready(self) -> tuple[str, Path]:
        """(binary, model_path) or raise LocalPolishError with the
        actionable one-liner for whatever is missing."""
        r = self.readiness()
        if not r["model_downloaded"]:
            raise LocalPolishError(
                f"local polish model {r['model']!r} is not downloaded — "
                "Settings → Models → AI polish model")
        if not r["binary"]:
            raise LocalPolishError(
                "no llama-server binary found — install an llama.cpp "
                "package or set ai.local_server_binary")
        return r["binary"], model_catalog.chat_model_path(r["model"])

    # -- lifecycle ---------------------------------------------------------

    def _log_file(self) -> Path:
        p = paths.cache_dir() / "llama-server.log"
        try:
            if p.exists() and p.stat().st_size > _LOG_MAX_BYTES:
                p.unlink()  # rotate by truncate: diagnostics, not data
        except OSError:
            pass
        return p

    def _spawn(self, binary: str, model_path: Path) -> int:
        """Start the child on a fresh ephemeral port; poll /health until
        ready. Returns the port; raises LocalPolishError on failure (the
        child is reaped either way)."""
        port = _free_loopback_port()
        argv = [binary,
                "--host", "127.0.0.1",
                "--port", str(port),
                "-m", str(model_path),
                "--alias", MODEL_ALIAS,
                "-c", str(DEFAULT_CTX)]
        logfile = self._log_file()
        logfile.parent.mkdir(parents=True, exist_ok=True)
        self._log(f"local polish: spawning {binary} (port {port}, "
                  f"{model_path.name}; log {logfile})")
        try:
            fh = open(logfile, "ab")
        except OSError:
            fh = subprocess.DEVNULL  # diagnostics are best-effort
        try:
            proc = subprocess.Popen(argv, stdout=fh, stderr=fh,
                                    stdin=subprocess.DEVNULL,
                                    close_fds=True)
        except OSError as e:
            if fh is not subprocess.DEVNULL:
                fh.close()
            raise LocalPolishError(
                f"could not start llama-server ({e})") from e
        deadline = time.monotonic() + self._startup_timeout_s
        url = f"http://127.0.0.1:{port}/health"
        try:
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise LocalPolishError(
                        "llama-server exited during startup — "
                        f"{self._log_tail(logfile)}")
                if self._health_probe(url):
                    self._proc = proc
                    self._port = port
                    return port
                time.sleep(0.25)
            raise LocalPolishError(
                f"llama-server not ready after {self._startup_timeout_s:.0f}s"
                f" — {self._log_tail(logfile)}")
        except BaseException:
            self._reap(proc)
            raise
        finally:
            if fh is not subprocess.DEVNULL:
                fh.close()

    @staticmethod
    def _log_tail(logfile: Path, limit: int = 300) -> str:
        try:
            text = logfile.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "no server log available"
        tail = text.strip().splitlines()[-3:]
        joined = " | ".join(ln.strip() for ln in tail if ln.strip())
        return joined[:limit] or "empty server log"

    @staticmethod
    def _reap(proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    pass

    def ensure_running(self) -> str:
        """The child's base URL (``http://127.0.0.1:<port>/v1``),
        spawning it first when needed. Thread-safe; the lock serializes
        concurrent spawn attempts (parallel takes share one child)."""
        with self._lock:
            binary, model_path = self._require_ready()
            proc = self._proc
            if proc is not None and proc.poll() is None \
                    and self._port is not None:
                if self._health_probe(
                        f"http://127.0.0.1:{self._port}/health"):
                    self.note_activity()
                    return f"http://127.0.0.1:{self._port}/v1"
            # not running (or unhealthy): a clean slate first
            if proc is not None:
                self._reap(proc)
                self._proc = None
                self._port = None
            port = self._spawn(binary, model_path)
            self.last_error = None
            self.note_activity()
            self._start_idle_watch()
            self._log(f"local polish: llama-server ready on 127.0.0.1:{port}")
            return f"http://127.0.0.1:{port}/v1"

    def stop(self, reason: str = "stop") -> None:
        """Terminate the child and its watcher. Zero footprint after."""
        with self._lock:
            self._idle_stop.set()
            proc, self._proc, self._port = self._proc, None, None
            watcher = self._idle_thread
        if watcher is not None and watcher.is_alive():
            watcher.join(timeout=2.0)  # the set event wakes it at once
        if proc is not None:
            self._reap(proc)
            self._log(f"local polish: llama-server stopped ({reason})")
        # a spawn that won the lock race during our join must not end up
        # watcher-less: re-arm now that the old thread is certainly dead
        with self._lock:
            if self._proc is not None:
                self._start_idle_watch()

    def shutdown(self) -> None:
        self.stop("daemon shutdown")

    def apply_setting_change(self) -> None:
        """ai.local_* keys flipped live: the only action needed is an
        immediate stop when the toggle went off (zero footprint)."""
        if not self.enabled():
            self.stop("local polish disabled")

    # -- idle watch ----------------------------------------------------------

    def idle_threshold(self) -> int:
        """ai.local_idle_s in seconds; 0 = never idle-stop."""
        try:
            return int((self.cfg.get("ai", {}) or {}).get("local_idle_s", 0)
                       or 0)
        except (TypeError, ValueError):
            return 0

    def note_activity(self) -> None:
        """Record polish activity (resets the idle clock)."""
        self.last_polish = time.monotonic()

    def maybe_idle_stop(self, now: float | None = None) -> None:
        """Terminate the child once no polish happened for
        ai.local_idle_s seconds. Never fires while a polish call is in
        flight, and never kills the WATCHER (a parked Event.wait thread
        is free; only stop()/shutdown() reaps it — so a re-spawn right
        after an idle stop always has a watcher). `now` is injectable
        for tests."""
        if now is None:
            now = time.monotonic()
        t = self.idle_threshold()
        with self._lock:
            if t <= 0 or self._proc is None or self._in_flight:
                return
            idle_s = now - self.last_polish
            # FP guard (engine_manager parity): an idle of at-least-t
            # must fire even when last_polish + t rounded a hair under.
            if idle_s < t - 1e-9:
                return
            proc, self._proc, self._port = self._proc, None, None
        self._reap(proc)
        self._log(f"local polish: llama-server idle {int(idle_s // 60)}m "
                  f">= {t}s - stopped (next polish re-spawns it)")

    def _idle_watch_loop(self) -> None:
        # exits ONLY on the stop event (maybe_idle_stop stops the server,
        # not the watcher — a parked Event.wait thread is free, and a
        # re-spawn right after an idle stop keeps its watcher this way)
        while not self._idle_stop.wait(
                max(5.0, min(60.0, self.idle_threshold() / 4.0))):
            try:
                self.maybe_idle_stop()
            except Exception as e:  # noqa: BLE001 - the watcher must survive
                self._log(f"WARN local-polish idle check failed: {e}")

    def _start_idle_watch(self) -> None:
        """One heartbeat thread per manager, first started at the first
        spawn and reaped only by stop()/shutdown() (which join it). With
        local_idle_s = 0 the child simply lives until stop()/shutdown()
        and no watcher ever starts."""
        if self.idle_threshold() <= 0:
            return
        t = self._idle_thread
        if t is not None and t.is_alive():
            return
        self._idle_stop = threading.Event()
        t = self._tasks.prepare("local-polish-idle", self._idle_watch_loop)
        self._idle_thread = t
        if t is not None:
            t.start()

    # -- polish plumbing -----------------------------------------------------

    def polish_client(self, cfg: dict) -> Any:
        """An AIClient for polish: the stock client when local polish is
        off (zero footprint — nothing is spawned), otherwise one pointed
        at the managed child, with an in-flight guard around .polish so
        the idle watcher can never kill the server mid-request.

        Raises LocalPolishError (an AIError subclass) when local polish
        is on but unusable, so the pipeline's existing fallback types the
        raw transcript."""
        from .ai.client import AIClient, AIError

        if not self.enabled():
            return AIClient(cfg)
        try:
            base_url = self.ensure_running()
        except LocalPolishError as e:
            self.last_error = str(e)
            raise AIError(str(e)) from e
        patched = copy.deepcopy(cfg)
        ai = patched.setdefault("ai", {})
        ai["base_url"] = base_url
        ai["model"] = MODEL_ALIAS
        ai["api_key"] = ""       # loopback child: no key, ever
        ai["api_key_env"] = ""   # and no env fallback either
        client = AIClient(patched)

        raw_polish = client.polish
        manager = self

        def _guarded_polish(text: str, system_prompt: str | None = None
                            ) -> str:
            with manager._lock:
                manager._in_flight += 1
            try:
                return raw_polish(text, system_prompt=system_prompt)
            finally:
                with manager._lock:
                    manager._in_flight -= 1
                manager.note_activity()

        client.polish = _guarded_polish  # type: ignore[method-assign]
        return client

    def polish_text(self, text: str, system_prompt: str | None = None) -> str:
        """One-shot polish through the managed child (eval harness /
        scripting convenience — the daemon path goes through
        polish_client)."""
        return self.polish_client(self.cfg).polish(text,
                                                   system_prompt=system_prompt)

    # -- status ---------------------------------------------------------------

    def status(self) -> dict:
        """Additive status block for the control route / doctor."""
        r = self.readiness()
        with self._lock:
            running = (self._proc is not None
                       and self._proc.poll() is None)
            port = self._port if running else None
            idle_s = (round(time.monotonic() - self.last_polish, 1)
                      if running else None)
        return {
            **r,
            "running": running,
            "port": port,
            "alias": MODEL_ALIAS,
            "policy_s": self.idle_threshold(),
            "idle_s": idle_s,
            "last_error": self.last_error,
        }
