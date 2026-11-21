"""Bundled local AI polish: an on-demand llama.cpp server (issue #21).

`ai.local_polish` routes the polish step through a llama-server process
the daemon owns: spawned on the first polished dictation, health-gated,
and idled down (stopped) after `ai.local_polish_idle_s` without use.
Toggle OFF = zero footprint: nothing is spawned, nothing stays resident.

Binary discovery (`find_llama_server`), in order:
  1. $SAYITERMANO_LLAMA_SERVER (explicit override)
  2. models_dir()/llama.cpp/bin/llama-server (a binary placed in the
     managed cache — the "bundled" location)
  3. llama-server on PATH (the distro llama.cpp package ships it)

The chat model is a CHAT_GGUF_CATALOG GGUF downloaded through the same
model manager as whisper (model_download.download_chat_gguf).

Budgets (documented + enforced):
  - cold start: START_TIMEOUT_S wall-clock bound on the spawn->healthy
    transition (a ~2.5 GB Q4 model loads in seconds from page cache,
    tens of seconds from cold disk on CPU); the measured duration is
    exposed as status().cold_start_s.
  - memory: while serving, llama-server holds roughly the model file
    size + context overhead (~3 GB for the qwen3-4b default). It is
    stopped after ai.local_polish_idle_s idle and on daemon shutdown,
    so the resting footprint is zero.
"""
from __future__ import annotations

import json
import os
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

__all__ = ["LocalAIError", "LocalAIManager", "find_llama_server",
           "START_TIMEOUT_S"]

Log = Callable[[str], None]

#: wall-clock bound on spawn -> /health ok (the cold-start budget).
START_TIMEOUT_S = 120.0
#: /health poll cadence while waiting for the model to load.
HEALTH_POLL_S = 0.25
#: single /health probe timeout (a busy loader can be slow to answer).
HEALTH_TIMEOUT_S = 2.0
#: idle-watcher cadence (the manager only checks; the policy is config).
IDLE_WATCH_S = 15.0
#: grace for SIGTERM before SIGKILL on stop.
TERM_GRACE_S = 5.0


class LocalAIError(RuntimeError):
    """Local polish is unavailable (disabled, model missing, binary
    missing, or the server failed to come up)."""


def find_llama_server() -> str | None:
    """Locate the llama-server binary (env override -> bundled cache
    location -> PATH). None when no candidate exists."""
    override = os.environ.get("SAYITERMANO_LLAMA_SERVER", "").strip()
    if override:
        p = Path(override).expanduser()
        return str(p) if p.is_file() else None
    bundled = paths.models_dir() / "llama.cpp" / "bin" / "llama-server"
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    return shutil.which("llama-server")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LocalAIManager:
    """Owns the optional llama-server child process (thread-safe).

    The daemon constructs one; the pipeline's polish step calls
    ``ensure_ready()`` on the process thread, the idle watcher calls
    ``maybe_idle_stop()`` on a supervised timer, shutdown/apply hooks
    call ``stop()``. With ``ai.local_polish`` off nothing ever spawns —
    ``ensure_ready()`` refuses without touching the system.
    """

    def __init__(self, cfg: dict, *, log: Log | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self._log = log or (lambda msg: None)
        self._clock = clock
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._port: int | None = None
        self._starting = False  # a spawn+health-wait is in flight
        self._last_error: str | None = None
        self._cold_start_s: float | None = None
        self.last_used: float | None = None

    # -- policy (read live from cfg) ---------------------------------------

    def enabled(self) -> bool:
        return bool(((self.cfg.get("ai", {}) or {})
                     .get("local_polish", False)))

    def model_name(self) -> str:
        raw = str((self.cfg.get("ai", {}) or {})
                  .get("local_polish_model", "") or "").strip()
        return raw if raw in model_catalog.CHAT_GGUF_CATALOG \
            else model_catalog.CHAT_MODEL_DEFAULT

    def idle_threshold(self) -> int:
        """Seconds of idle before the server is stopped; 0 = keep it."""
        try:
            return int((self.cfg.get("ai", {}) or {})
                       .get("local_polish_idle_s", 300) or 0)
        except (TypeError, ValueError):
            return 300

    # -- readiness ----------------------------------------------------------

    def ensure_ready(self, timeout: float | None = None) -> dict:
        """A healthy local endpoint: spawns llama-server on demand and
        waits for /health. Returns {"base_url", "model"} on success;
        raises LocalAIError with an actionable message otherwise.
        Reuses a healthy running server (no respawn per dictation).

        The health WAIT happens outside the manager lock: a cold start
        can take the full START_TIMEOUT_S budget, and status()/stop()
        must stay responsive on the control socket meanwhile. Only one
        caller spawns; concurrent callers wait for the in-flight start
        and then re-check health."""
        if not self.enabled():
            raise LocalAIError(
                "local polish is off (ai.local_polish = false)")
        name = self.model_name()
        if not model_catalog.chat_gguf_downloaded(name):
            raise LocalAIError(
                f"{name} is not downloaded — get it in Settings → Models")
        binary = find_llama_server()
        if not binary:
            raise LocalAIError(
                "llama-server not found — install llama.cpp (provides "
                "llama-server) or point SAYITERMANO_LLAMA_SERVER at a "
                "binary")
        budget = timeout if timeout is not None else START_TIMEOUT_S
        while True:
            with self._lock:
                if self._healthy():
                    self.note_used()
                    return self._endpoint(name)
                if self._starting:
                    starter_alive = True  # wait below, outside the lock
                else:
                    starter_alive = False
                    self._starting = True
                    self._stop_locked(
                        reason="restart after unhealthy state")
                    try:
                        self._launch_locked(binary, name)
                    except BaseException:
                        self._starting = False  # never wedge the waiters
                        raise
            if starter_alive:
                # another thread owns the cold start; poll alongside it
                # (bounded by the same budget from THIS call's start)
                if self._await_health(budget):
                    with self._lock:
                        self.note_used()
                        return self._endpoint(name)
                # starter failed (it owns the error bookkeeping) or the
                # budget ran out - surface the recorded reason
                with self._lock:
                    err = self._last_error or \
                        "llama-server did not become healthy in time"
                    raise LocalAIError(err)
            # we are the starter: wait for health outside the lock
            started = time.monotonic()
            if self._await_health(budget):
                with self._lock:
                    self._cold_start_s = round(
                        time.monotonic() - started, 2)
                    self._last_error = None
                    self._starting = False
                    self.note_used()
                    self._log(f"local polish: llama-server ready on "
                              f"port {self._port} (cold start "
                              f"{self._cold_start_s}s)")
                    return self._endpoint(name)
            with self._lock:
                tail = self._stderr_tail()
                self._stop_locked(reason="failed to become healthy")
                self._starting = False
                self._last_error = (
                    f"llama-server did not become healthy within "
                    f"{budget:g}s" + (f": {tail}" if tail else ""))
                raise LocalAIError(self._last_error)

    def _launch_locked(self, binary: str, name: str) -> None:
        """Start the child (fast — no waiting here). Any failure kills
        the child and raises LocalAIError."""
        info = model_catalog.CHAT_GGUF_CATALOG[name]
        model_path = model_catalog.chat_gguf_path(name)
        port = _free_port()
        argv = [
            binary,
            "-m", str(model_path),
            "--host", "127.0.0.1",
            "--port", str(port),
            "-c", str(int(info.get("ctx", 4096))),
            "--alias", str(info.get("serve_as") or name),
        ]
        try:
            self._log(f"local polish: starting llama-server "
                      f"({name}, port {port})")
            self._proc = subprocess.Popen(
                argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL)
        except OSError as e:
            self._last_error = f"failed to launch llama-server: {e}"
            raise LocalAIError(self._last_error) from e
        self._port = port

    def _await_health(self, budget_s: float) -> bool:
        """Poll /health until ok, the child dies, or the budget runs
        out. Lock-free by design (the lock must stay free for status()
        and stop() on other threads)."""
        deadline = time.monotonic() + budget_s
        while time.monotonic() < deadline:
            proc, port = self._proc, self._port
            if proc is None or port is None or proc.poll() is not None:
                return False  # died, or the starter stopped it
            if self._probe_health(port):
                return True
            time.sleep(HEALTH_POLL_S)
        return False

    def _endpoint(self, name: str) -> dict:
        info = model_catalog.CHAT_GGUF_CATALOG[name]
        return {"base_url": f"http://127.0.0.1:{self._port}/v1",
                "model": str(info.get("serve_as") or name)}

    def _stderr_tail(self) -> str:
        """Last bytes of the child's stderr (only valid once it is dead —
        reading a live pipe would block)."""
        proc = self._proc
        if proc is None or proc.stderr is None or proc.poll() is None:
            return ""
        try:
            raw = proc.stderr.read() or b""
        except (OSError, ValueError):
            return ""
        return raw.decode(errors="replace").strip()[-300:]

    def _healthy(self) -> bool:
        """A live child answering ok on the managed port (see
        _probe_health for the body semantics)."""
        if self._proc is None or self._port is None:
            return False
        if self._proc.poll() is not None:
            return False
        return self._probe_health(self._port)

    def _probe_health(self, port: int) -> bool:
        """GET /health on a loopback port. Bodies with an explicit
        non-ok status (e.g. "loading model") keep waiting; a non-JSON
        200 (older builds) counts as healthy."""
        url = f"http://127.0.0.1:{port}/health"
        try:
            with urllib.request.urlopen(url, timeout=HEALTH_TIMEOUT_S) as r:
                if r.status != 200:
                    return False
                body = r.read().decode(errors="replace")
        except (OSError, ValueError):
            return False
        try:
            status = json.loads(body or "{}").get("status")
        except ValueError:
            return True  # non-JSON 200 from an older build
        return status in (None, "ok")

    # -- lifecycle -----------------------------------------------------------

    def note_used(self) -> None:
        """Polish activity marker (resets the idle clock)."""
        self.last_used = self._clock()

    def maybe_idle_stop(self, now: float | None = None) -> bool:
        """Stop the server once idle exceeded the policy threshold.
        True when a stop happened; a no-op when off/never-used/within
        the window (the zero-resident guarantee after use)."""
        if self.idle_threshold() <= 0:
            return False
        now = self._clock() if now is None else now
        with self._lock:
            if self._proc is None or self.last_used is None:
                return False
            if self._proc.poll() is not None:
                self._reap()
                return False
            idle = now - self.last_used
            if idle < self.idle_threshold():
                return False
            self._stop_locked(
                reason=f"idle {int(idle)}s >= {self.idle_threshold()}s")
            return True

    def stop(self) -> None:
        """Stop the server now (daemon shutdown / toggle turned off)."""
        with self._lock:
            self._stop_locked(reason="stop requested")

    def _stop_locked(self, reason: str) -> None:
        proc, self._proc = self._proc, None
        self._port = None
        if proc is None:
            return
        self._log(f"local polish: stopping llama-server ({reason})")
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=TERM_GRACE_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=TERM_GRACE_S)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    pass
        for pipe in (proc.stderr, proc.stdout):
            if pipe is not None:
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass

    def _reap(self) -> None:
        """Clear bookkeeping for a child that died on its own."""
        proc, self._proc = self._proc, None
        self._port = None
        if proc is not None and proc.stderr is not None:
            try:
                proc.stderr.close()
            except (OSError, ValueError):
                pass

    def shutdown(self) -> None:
        """Daemon shutdown path: stop the child (bounded)."""
        self.stop()

    # -- idle watcher ---------------------------------------------------------

    def watch_loop(self, stop_event: threading.Event) -> None:
        """Supervised-thread body: periodic maybe_idle_stop checks."""
        while not stop_event.wait(IDLE_WATCH_S):
            try:
                self.maybe_idle_stop()
            except Exception as e:  # noqa: BLE001 - the watcher must survive
                self._log(f"WARN local polish idle check failed: {e}")

    # -- status ----------------------------------------------------------------

    def status(self) -> dict:
        """Additive status block for the control socket + doctor.
        Never blocks on the manager lock: a cold start can hold it for
        the whole START_TIMEOUT_S budget, and the socket's status route
        must answer meanwhile (degraded but honest: "starting")."""
        acquired = self._lock.acquire(timeout=0.2)
        try:
            if not acquired:
                return {"enabled": self.enabled(),
                        "model": self.model_name(),
                        "running": False, "starting": True,
                        "port": None, "base_url": None,
                        "cold_start_s": self._cold_start_s,
                        "idle_policy_s": self.idle_threshold(),
                        "last_error": self._last_error}
            running = (self._proc is not None
                       and self._proc.poll() is None)
            out: dict[str, Any] = {
                "enabled": self.enabled(),
                "model": self.model_name(),
                "model_downloaded": model_catalog.chat_gguf_downloaded(
                    self.model_name()),
                "binary": find_llama_server(),
                "running": running,
                "starting": self._starting,
                "port": self._port if running else None,
                "base_url": (f"http://127.0.0.1:{self._port}/v1"
                             if running else None),
                "cold_start_s": self._cold_start_s,
                "idle_policy_s": self.idle_threshold(),
                "last_error": self._last_error,
            }
            if running and self.last_used is not None:
                out["idle_s"] = round(self._clock() - self.last_used, 1)
            return out
        finally:
            if acquired:
                self._lock.release()
