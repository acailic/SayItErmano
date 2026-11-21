"""Bundled local AI polish: a supervised llama.cpp server (issue #21).

One toggle (``ai.local_polish``, "Local polish (works offline)") turns
the optional cloud polish step fully local: the daemon spawns the pinned
``llama-server`` from the models cache (Settings -> Models downloads it
with the chat GGUF, one click), waits for its loopback ``/health``, and
points the AI client at it. After ``ai.local_idle_stop_s`` without a
dictation the server is stopped again - and with the toggle off nothing
is spawned at all (zero footprint, enforced by tests).

Endpoint shape: the server binds 127.0.0.1 only, on a FIXED port
(``ai.local_port``, default 8931) so every consumer (daemon pipeline,
rewrite, command mode, the GTK test button, the eval harness) can derive
the identical base URL ``http://127.0.0.1:<port>/v1`` without discovery.
No API key is ever sent to the local server.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .. import model_catalog, paths

DEFAULT_PORT = 8931
#: how long ensure_ready() waits for /health after spawn (cold start on a
#: slow CPU: ~2.5 GB of weights to mmap + warm; 16 threads load in ~5-15 s)
START_TIMEOUT_S = 90.0
HEALTH_TIMEOUT_S = 2.0

Log = Callable[[str], None]


class LocalServerError(RuntimeError):
    """Actionable failure of the local polish server (message is shown
    as-is in logs/notifications)."""


def local_polish_on(cfg: dict) -> bool:
    ai = (cfg.get("ai", {}) or {}) if isinstance(cfg, dict) else {}
    return bool(ai.get("enabled")) and bool(ai.get("local_polish"))


def local_base_url(cfg: dict) -> str:
    ai = cfg.get("ai", {}) or {}
    try:
        port = int(ai.get("local_port", DEFAULT_PORT))
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return f"http://127.0.0.1:{port}/v1"


def local_ai_overrides(cfg: dict) -> dict:
    """The [ai] patch that redirects the client to the bundled server
    while local polish is on: base_url/model from the local catalog, no
    API key (never send the cloud key to a local process)."""
    ai = cfg.get("ai", {}) or {}
    return {
        "base_url": local_base_url(cfg),
        "model": str(ai.get("local_model")
                     or model_catalog.CHAT_DEFAULT_MODEL),
        "api_key": "",
        "api_key_env": "",
    }


def resolve_server_binary() -> Path:
    """The bundled llama-server, else one on PATH ( llama.cpp installed
    by the user / distro package). LocalServerError when neither exists."""
    if model_catalog.llama_runtime_downloaded():
        return model_catalog.llama_server_path()
    found = shutil.which("llama-server")
    if found:
        return Path(found)
    raise LocalServerError(
        "the bundled llama.cpp server is not downloaded yet - get it in "
        "Settings → Models (Local AI polish), or install llama-server "
        "on PATH")


def _rss_kb(pid: int) -> int | None:
    """Resident memory of the server process (procfs, best-effort)."""
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def probe_health(port: int, timeout: float = HEALTH_TIMEOUT_S) -> bool:
    """True when the llama-server answers /health on loopback."""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


class LocalServer:
    """Supervises the bundled llama-server child process.

    Lifecycle mirrors SpeechEngineManager's idle-unload: ``ensure_ready``
    spawns on demand and waits for readiness; a watcher thread stops the
    process after ``ai.local_idle_stop_s`` idle; ``apply_settings``
    (set-config hot-apply) stops everything the moment the toggle goes
    off. ``command`` overrides the binary (tests inject a stub server).
    """

    def __init__(self, cfg: dict, *, command: list[str] | None = None,
                 log: Log | None = None, start_timeout_s: float | None = None,
                 on_stop: Callable[[], None] | None = None):
        self._cfg = cfg
        self._command_override = command
        self._log = log or (lambda msg: None)
        self._start_timeout = start_timeout_s or START_TIMEOUT_S
        self._on_stop = on_stop
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._port = 0
        self._spawned_model = ""
        self._starting = False
        self._error: str | None = None
        self._started_at = 0.0
        self._last_activity = 0.0
        self._idle_stop = threading.Event()
        self._idle_thread: threading.Thread | None = None

    # -- policy -----------------------------------------------------------

    def idle_policy_s(self) -> int:
        ai = self._cfg.get("ai", {}) or {}
        try:
            v = int(ai.get("local_idle_stop_s", 180) or 0)
        except (TypeError, ValueError):
            v = 180
        return v if v >= 30 else (0 if v == 0 else 30)

    def model_name(self) -> str:
        ai = self._cfg.get("ai", {}) or {}
        return str(ai.get("local_model") or
                   model_catalog.CHAT_DEFAULT_MODEL)

    def _port_of_cfg(self) -> int:
        ai = self._cfg.get("ai", {}) or {}
        try:
            return int(ai.get("local_port", DEFAULT_PORT))
        except (TypeError, ValueError):
            return DEFAULT_PORT

    # -- state ------------------------------------------------------------

    def status(self) -> dict:
        """Everything the tray/status route/UI shows. `enabled` reflects
        the toggle AND ai.enabled (the polish step must be on for the
        server to ever matter)."""
        with self._lock:
            proc = self._proc
            running = proc is not None and proc.poll() is None
            st = {
                "enabled": local_polish_on(self._cfg),
                "toggle": bool((self._cfg.get("ai", {}) or {})
                               .get("local_polish")),
                "running": running,
                "starting": self._starting and not running,
                "model": self.model_name(),
                "model_downloaded": model_catalog.chat_model_downloaded(
                    self.model_name()),
                "runtime_downloaded": model_catalog.llama_runtime_downloaded(),
                "port": self._port or self._port_of_cfg(),
                "policy_s": self.idle_policy_s(),
                "error": self._error,
            }
            if running:
                st["pid"] = proc.pid
                st["uptime_s"] = round(time.monotonic() - self._started_at, 1)
                st["idle_s"] = round(
                    time.monotonic() - self._last_activity, 1)
                st["rss_kb"] = _rss_kb(proc.pid)
            return st

    def _set_error(self, msg: str | None) -> None:
        with self._lock:
            self._error = msg

    # -- spawn / stop -----------------------------------------------------

    def _server_command(self, port: int) -> list[str]:
        if self._command_override is not None:
            # tests inject a stub server; it gets the port like the real
            # one (last flag wins for llama-server argument parsing)
            return [*self._command_override, "--port", str(port)]
        binary = resolve_server_binary()
        model = model_catalog.chat_model_path(self.model_name())
        ctx = int(model_catalog.CHAT_CATALOG[self.model_name()]
                  .get("ctx", 8192))
        threads = max(2, (os.cpu_count() or 4) // 2)
        return [str(binary), "-m", str(model), "--host", "127.0.0.1",
                "--port", str(port), "-c", str(ctx), "-t", str(threads),
                "-np", "1", "--no-webui", "--alias", self.model_name()]

    def ensure_ready(self, timeout: float | None = None) -> dict:
        """Server up and healthy on the configured port (spawn + wait).
        Returns the AIClient overrides ({base_url, model, ...}); raises
        LocalServerError with an actionable message. Idempotent."""
        timeout = timeout if timeout is not None else self._start_timeout
        if not local_polish_on(self._cfg):
            raise LocalServerError(
                "local polish is off (enable it in Settings → AI)")
        port = self._port_of_cfg()
        name = self.model_name()
        if not model_catalog.chat_model_downloaded(name):
            raise LocalServerError(
                f"the local polish model ({name}) is not downloaded yet - "
                "get it in Settings → Models, 'Local AI polish'")
        with self._lock:
            if (self._proc is not None and self._proc.poll() is None
                    and self._port == port and probe_health(port)):
                self._last_activity = time.monotonic()
                return local_ai_overrides(self._cfg)
            self._starting = True
        self._set_error(None)
        try:
            self._spawn(port)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self._proc is None or self._proc.poll() is not None:
                    raise LocalServerError(
                        f"llama-server exited during startup "
                        f"(exit {self._proc.returncode if self._proc else '?'}) "
                        f"- see {self._log_path()}")
                if probe_health(port):
                    with self._lock:
                        self._last_activity = time.monotonic()
                    self._start_idle_watch()
                    self._log(f"local polish server ready on 127.0.0.1:{port} "
                              f"(pid {self._proc.pid}, model {name})")
                    return local_ai_overrides(self._cfg)
                time.sleep(0.25)
            raise LocalServerError(
                f"llama-server not healthy after {int(timeout)} s "
                f"- see {self._log_path()}")
        except LocalServerError as e:
            self._set_error(str(e))
            self.stop("startup failed")
            raise
        except Exception as e:  # noqa: BLE001 - wrap unexpected failures
            self._set_error(str(e))
            self.stop("startup failed")
            raise LocalServerError(
                f"cannot start the local polish server: {e}") from e
        finally:
            with self._lock:
                self._starting = False

    def _log_path(self) -> Path:
        return paths.cache_dir() / "llama-server.log"

    def _spawn(self, port: int) -> None:
        try:
            cmd = self._server_command(port)
        except LocalServerError:
            raise
        except Exception as e:  # noqa: BLE001 - actionable spawn errors
            raise LocalServerError(f"cannot start llama-server: {e}") from e
        self.stop("restarting")  # never two servers
        log_path = self._log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            log = open(log_path, "ab")  # noqa: SIM115 - fd passes to child
        except OSError:
            log = None
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL,
                stdout=log if log is not None else subprocess.DEVNULL,
                stderr=subprocess.STDOUT, close_fds=True)
        except OSError as e:
            raise LocalServerError(f"cannot start llama-server: {e}") from e
        finally:
            if log is not None:
                log.close()  # the child keeps its inherited copy
        with self._lock:
            self._port = port
            self._spawned_model = self.model_name()
            self._started_at = time.monotonic()
            self._last_activity = self._started_at
        self._log(f"spawned llama-server (pid {self._proc.pid}) on "
                  f"127.0.0.1:{port}")

    def stop(self, reason: str = "") -> None:
        """Terminate the server (SIGTERM, then kill) and reap it."""
        with self._lock:
            proc, self._proc = self._proc, None
            self._starting = False
        self._stop_idle_watch()
        if proc is None:
            return
        note = f" ({reason})" if reason else ""
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except (OSError, ValueError):
            pass
        self._log(f"stopped llama-server pid {proc.pid}{note}")
        if self._on_stop:
            try:
                self._on_stop()
            except Exception:  # noqa: BLE001,S110 - best-effort callback
                pass

    # -- idle stop ----------------------------------------------------------

    def touch_activity(self) -> None:
        with self._lock:
            self._last_activity = time.monotonic()

    def maybe_idle_stop(self, now: float | None = None) -> bool:
        """Stop the server once idle for policy seconds. True when it
        fired. Never fires while another ensure/spawn is starting."""
        if now is None:
            now = time.monotonic()
        policy = self.idle_policy_s()
        if policy <= 0:
            return False
        with self._lock:
            if self._proc is None or self._starting:
                return False
            idle = now - self._last_activity
            if idle < policy - 1e-9:  # same FP guard as engine_manager
                return False
        self.stop(f"idle {int(idle)}s >= {policy}s")
        return True

    def _idle_watch_loop(self) -> None:
        while not self._idle_stop.wait(
                max(5.0, min(60.0, self.idle_policy_s() / 4.0))):
            try:
                self.maybe_idle_stop()
            except Exception as e:  # noqa: BLE001 - the watcher survives
                self._log(f"WARN local-ai idle check failed: {e}")

    def _start_idle_watch(self) -> None:
        if self.idle_policy_s() <= 0:
            return
        if self._idle_thread is not None and self._idle_thread.is_alive():
            return
        self._idle_stop = threading.Event()
        self._idle_thread = threading.Thread(
            target=self._idle_watch_loop, name="local-ai-idle", daemon=True)
        self._idle_thread.start()

    def _stop_idle_watch(self) -> None:
        self._idle_stop.set()
        t = self._idle_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2)
        self._idle_thread = None

    # -- set-config hot-apply ----------------------------------------------

    def apply_settings(self) -> dict:
        """React to [ai] changes: toggle/model/port edits. Off = stop
        now (zero footprint); a changed model/port = stop, respawn on
        the next dictation; on = nothing until the next dictation."""
        if not local_polish_on(self._cfg):
            self.stop("local polish off")
            return {"stopped": True}
        running = self._proc is not None and self._proc.poll() is None
        if running and (self.model_name() != self._spawned_model
                        or self._port_of_cfg() != self._port):
            self.stop("local polish settings changed")
            return {"respawn_pending": True}
        self.touch_activity()
        if running:
            self._start_idle_watch()
        return {"active": True}


def status_for_config(cfg: dict) -> dict:
    """Status when no supervisor exists (other processes): the toggle
    state plus what the daemon WOULD use - honest without spawning."""
    name = str((cfg.get("ai", {}) or {}).get("local_model")
               or model_catalog.CHAT_DEFAULT_MODEL)
    port = int((cfg.get("ai", {}) or {}).get("local_port") or DEFAULT_PORT)
    return {
        "enabled": local_polish_on(cfg),
        "toggle": bool((cfg.get("ai", {}) or {}).get("local_polish")),
        "running": probe_health(port) if local_polish_on(cfg) else False,
        "model": name,
        "model_downloaded": model_catalog.chat_model_downloaded(name),
        "runtime_downloaded": model_catalog.llama_runtime_downloaded(),
        "port": port,
    }
