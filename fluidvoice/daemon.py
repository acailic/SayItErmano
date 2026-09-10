"""The SayItErmano daemon: hotkey -> record -> transcribe -> polish -> type.

Structure:
  Daemon            composition root + control router: state flags, hotkey/
                    tray/socket wiring, request dispatch
  SpeechEngineManager  engine lifecycle: load/warm/reload/idle-unload/
                    select/delete + language resolution
                    (fluidvoice/engine_manager.py, P1.2)
  CommandCoordinator command conversation: proposal, confirmation,
                    timeout, panel, history (fluidvoice/command_coord.py)
  CaptureCoordinator take lifecycle: recording state, preview, VAD,
                    PCM/stall/max-duration timers, media pause, stop and
                    cancel (fluidvoice/capture.py, P1.2)
  DictationPipeline one utterance: wav -> transcribe -> post-process -> optional
                    AI polish -> insert -> history (lives in fluidvoice/
                    pipeline.py since audit C5a; re-exported below). Every
                    step is injectable so the whole flow is unit-testable
                    without audio, X11 or GPU.
"""
from __future__ import annotations

import os
import signal
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from . import __version__, backends, control, insertion, paths, ui
from . import history as history_mod
from . import session as session_mod
from . import update as update_mod
from .ai.client import AIClient, AIError  # noqa: F401 (tests patch dm.AIClient)
from .audio_utils import duration_seconds, is_silent
from .backends.base import Transcript
from .capture import CaptureCoordinator
from .command_coord import CommandCoordinator
from .config import load_config
from .engine_manager import SpeechEngineManager
from .micmon import match_priority as micmon_match_priority
from .pipeline import DictationPipeline, confidence_band, log  # noqa: F401
from .processing import post_process
from .recorder import Recorder, RecorderError
from .runtime_tasks import RuntimeTasks

BackendFactory = Callable[[dict], Any]


class Daemon:
    def __init__(self, cfg: dict | None = None, *,
                 recorder: Recorder | None = None,
                 backend_factory: BackendFactory = backends.load_backend,
                 pipeline_factory=DictationPipeline,
                 command_session_factory=None,
                 use_hotkey: bool = True, use_sounds: bool = True):
        self.cfg = cfg or load_config()
        self.use_hotkey = use_hotkey
        self.use_sounds = use_sounds
        self.recorder = recorder or Recorder(
            command=self.cfg["recording"]["command"],
            device=self.cfg["recording"].get("device", ""),
            sample_rate=self.cfg["recording"].get("sample_rate", 16000))
        self._backend_factory = backend_factory
        self._pipeline_factory = pipeline_factory
        self._command_session_factory = command_session_factory
        self.busy = False  # transcription/insertion in flight
        self.last_result: dict = {}
        # RuntimeTasks: every timer/thread the daemon spawns is named,
        # exception-reported and joined at shutdown (fluidvoice/
        # runtime_tasks.py; plan P1.2). The handles below stay Timer/
        # Thread objects so call sites and tests keep identity checks.
        self._tasks = RuntimeTasks(on_exception=self._on_task_exception)
        self._lock = threading.Lock()  # shared daemon-state lock
        # SpeechEngineManager: backend load/warm/reload/idle-unload/select/
        # delete + the runtime language cycle (P1.2). Daemon exposes the
        # live backend/warmup through properties below.
        self._engines = SpeechEngineManager(
            self.cfg, self._tasks,
            backend_factory=self._backend_factory, lock=self._lock, log=log,
            notify=lambda title, body: ui.notify(
                title, body, enabled=self.cfg["notifications"]["enabled"]),
            is_locked=lambda: self._locked,
            is_take_active=lambda: self.recording or self.busy,
            on_unload=self._refresh_tray,
            on_language_change=lambda lang: (self._capture.announce_language(lang),
                                             self._refresh_tray()))
        # CommandCoordinator: the command-conversation state machine
        # (proposal/confirmation/timeout/panel/history, P1.2). Daemon
        # routes control actions and hotkey presses to it.
        self._commands = CommandCoordinator(
            self.cfg, self._tasks, self._lock,
            is_busy=lambda: self.busy,
            set_busy=self._set_busy,
            is_recording=lambda: self.recording,
            log=log,
            notify=lambda title, body: ui.notify(
                title, body, enabled=self.cfg["notifications"]["enabled"]),
            session_factory=self._command_session_factory,
            arm_escape=self._arm_command_escape)
        # CaptureCoordinator: the take lifecycle - recording state,
        # preview, VAD, PCM/stall/max-duration timers, media pause, stop
        # and cancel (P1.2). Daemon exposes the live recording flag
        # through the property below.
        self._capture = CaptureCoordinator(
            self.cfg, self._tasks, self._lock,
            recorder_provider=lambda: self.recorder,
            log=log,
            notify=lambda title, body: ui.notify(
                title, body, enabled=self.cfg["notifications"]["enabled"]),
            play_sound=lambda which: ui.play_sound(
                which, self.cfg["sounds"]["volume"],
                self.use_sounds and self.cfg["sounds"]["enabled"]),
            on_recording_change=self._tray_recording,
            on_take_start=self._on_take_start,
            on_take_complete=self._on_take_complete,
            on_activity=self._engines.touch_activity,
            language_provider=lambda: self._engines.language_detail()[0],
            backend_provider=lambda: self._engines.backend,
            on_copy_last=self._copy_last_transcript,
            on_paste_last=self.paste_last)
        self._command_hotkey = None
        self._paste_hotkey = None
        self._language_hotkey = None
        self._extra_hotkeys: list = []
        self._tray: Any = None
        self._micmon: Any = None  # input-device watcher (micmon.MicMonitor)
        self._mic_missing_logged = False  # warn-once latch for reselects
        self._hotkey = None
        self._mouse_ptt: Any = None  # MousePTTListener when configured
        self._locked = False  # session locked/suspended (lockmon flips)
        self._lockmon: Any = None  # lockmon.LockMonitor when active
        self._update: Any = None  # update.UpdateChecker when active
        self._rewrite_hotkey = None
        self._srv: Any = None
        self._process_thread: threading.Thread | None = None
        self._session = session_mod.probe()  # session type + capabilities
        self._evdev_ptt: Any = None  # optional evdev push-to-talk (wayland)

    # -- engine state (owned by SpeechEngineManager; daemon-visible) -------

    @property
    def backend(self) -> Any:
        """The loaded speech backend (None = lazy / idle-unloaded)."""
        return self._engines.backend

    @backend.setter
    def backend(self, value: Any) -> None:
        self._engines.backend = value

    @property
    def warmup(self) -> dict:
        """Engine load status dict (running/error/model) for status/UI."""
        return self._engines.warmup

    @warmup.setter
    def warmup(self, value: dict) -> None:
        self._engines.warmup = value

    @property
    def recording(self) -> bool:
        """A take is being recorded (owned by CaptureCoordinator)."""
        return self._capture.recording

    @recording.setter
    def recording(self, value: bool) -> None:
        self._capture.recording = value

    def _on_task_exception(self, name: str, exc: BaseException) -> None:
        """RuntimeTasks exception reporter: a failed supervised callback
        is logged in full, never raised unhandled in its worker thread
        (which the suite's thread-exception gate turns into a failure)."""
        log(f"WARN runtime task {name!r} failed: "
            f"{exc.__class__.__name__}: {exc}\n"
            + "".join(traceback.format_exception(exc)).rstrip())

    def _set_busy(self, busy: bool) -> None:
        """Coordinator hook: flip the daemon's busy flag (callers that
        need atomicity hold the shared lock)."""
        self.busy = busy

    def _arm_command_escape(self, active: bool) -> None:
        """Coordinator hook: arm/disarm the command hotkey's Escape grab
        while a proposal is pending."""
        if self._command_hotkey:
            try:
                self._command_hotkey.set_recording(active)
            except Exception:
                pass

    # -- lifecycle -----------------------------------------------------------

    def run(self) -> None:
        log(f"SayItErmano v{__version__} starting")
        self._session = session_mod.probe()
        self._log_session()
        self._sweep_stale_tmp()
        self._engines.startup_load()
        # Idle unload watcher: only exists when model.idle_unload_s > 0.
        self._engines.start_idle_watch()

        if self.use_hotkey:
            self._start_hotkey()
        self._start_mouse_ptt()
        self._start_evdev_ptt()

        self._start_tray()
        self._start_micmon()
        self._start_lockmon()
        self._start_update_checker()
        self._maybe_first_run_onboard()

        ready = threading.Event()
        self._srv = control.serve(self.handle_request, ready=ready)
        ready.wait(timeout=5)
        log(f"control socket: {paths.socket_path()}")

        import os as _os
        cfg_shown = _os.environ.get("SAYITERMANO_CONFIG") or paths.config_file()
        log("ready - press the hotkey to dictate "
            f"(or run `sayit-ermano toggle`; config: {cfg_shown})")

        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        try:
            while not stop.is_set():
                time.sleep(0.3)
        finally:
            self.shutdown()

    def _log_session(self) -> None:
        """One honest startup line: the session type and the capability
        backends it resolves to (fluidvoice/session.py). The wayland
        bind-script hint is logged by _start_hotkey, not duplicated here."""
        info = self._session
        caps = session_mod.capabilities(info, cfg=self.cfg)
        where = f"{info.type}" + (f" ({info.desktop})" if info.desktop else "")
        summary = " ".join(f"{k}={v}" for k, v in caps.items())
        log(f"session: {where} - capabilities: {summary}")

    def _start_tray(self) -> None:
        """Panel/tray icon (StatusNotifierItem): click toggles dictation,
        right-click opens the dropdown menu, tooltip shows state + hotkey."""
        if not self.cfg["general"].get("tray_enabled", True):
            return
        try:
            from .tray import TrayIcon
            tray = TrayIcon(on_activate=self.toggle,
                            on_secondary=self._open_settings,
                            tooltip=self._tray_tooltip,
                            build_menu=self._build_tray_menu,
                            log=log)
            if tray.start():
                self._tray = tray
                log("tray icon active (click = dictate, right-click = menu)")
            else:
                log("tray unavailable on this desktop - running headless")
        except Exception as e:
            log(f"WARN tray unavailable: {e}")

    def _build_tray_menu(self) -> list:
        """Native dropdown model, mirroring the macOS menu bar menu:
        status, cancel, copy last transcript, settings, microphone, quit."""
        with self._lock:
            recording, busy = self.recording, self.busy
        hk = self.cfg["hotkey"].get("key", "")
        state = ("Recording…" if recording else
                 "Processing…" if busy else "Ready to Record")
        status = f"{state} ({hk})" if hk else state
        text = (self.last_result or {}).get("text") or ""
        if not text:
            try:
                entries = history_mod.tail(1)
                text = entries[0].get("text", "") if entries else ""
            except Exception:
                text = ""
        device = self.cfg["recording"].get("device", "")
        mics = [{"kind": "check", "label": "Auto (system default)",
                 "checked": not device,
                 "action": lambda: self._set_device("")}]
        from .micmon import sort_by_priority
        from .tray import list_microphones
        mic_names = self.cfg["recording"].get("mic_priority") or []
        for m in sort_by_priority(list_microphones(), mic_names):
            mics.append({"kind": "check", "label": m["description"],
                         "checked": device == m["name"],
                         "action": lambda n=m["name"]: self._set_device(n)})
        return [
            {"kind": "item", "label": status, "enabled": False},
            {"kind": "item", "label": "Cancel Dictation (Esc)",
             "enabled": recording, "action": self.cancel},
            {"kind": "item", "label": "Copy Last Transcript",
             "enabled": bool(text), "action": self._copy_last_transcript},
            {"kind": "separator"},
            {"kind": "item", "label": "Settings…",
             "action": lambda: self._open_settings()},
            {"kind": "item", "label": "History",
             "action": lambda: self._open_settings("/history")},
            {"kind": "item", "label": "Microphone", "children": mics},
            {"kind": "separator"},
            {"kind": "item", "label": "Quit SayItErmano",
             "action": self._quit_gracefully},
        ]

    def _copy_last_transcript(self) -> None:
        text = (self.last_result or {}).get("text") or ""
        if not text:
            from . import history as history_mod
            entries = history_mod.tail(1)
            text = entries[0].get("text", "") if entries else ""
        if not text:
            ui.notify("SayItErmano", "Nothing to copy",
                      enabled=self.cfg["notifications"]["enabled"])
            return
        import shutil
        import subprocess
        tool = next((t for t in ("xclip", "xsel", "wl-copy")
                     if shutil.which(t)), None)
        if not tool:
            ui.notify("SayItErmano", "No clipboard tool found (install xclip)",
                      enabled=self.cfg["notifications"]["enabled"])
            return
        args = ([tool, "-selection", "clipboard"] if tool == "xclip"
                else [tool, "-clipboard", "-in"] if tool == "xsel"
                else [tool])
        try:
            subprocess.run(args, input=text.encode(), check=True, timeout=3,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            log(f"copied last transcript ({len(text)} chars)")
        except Exception as e:
            log(f"copy failed: {e}")

    def _set_device(self, device: str) -> None:
        with self._lock:
            if self.recording or self.busy:
                log("cannot switch microphone while dictating")
                return
            self.cfg["recording"]["device"] = device
            try:
                from .config import save_config
                save_config(self.cfg)
            except Exception as e:
                log(f"WARN could not save microphone choice: {e}")
            self._rebuild_recorder()
            log(f"microphone set to {device or 'auto'}")

    # -- input-device monitoring (mic auto-switch) ---------------------------

    def _start_micmon(self, poll=None, interval=None) -> None:
        """Best-effort input-device watcher (like the tray): diff-polls
        pactl sources and auto-switches when the configured device vanishes
        and a `recording.mic_priority` pattern matches (see micmon.py).
        poll/interval overrides exist for tests."""
        from .micmon import MicMonitor
        kwargs: dict = {}
        if poll is not None:
            kwargs["poll"] = poll
        if interval is not None:
            kwargs["interval"] = interval
        mon = MicMonitor(on_change=self._on_sources_changed, log=log, **kwargs)
        if not mon.start():
            return  # already logged "mic monitoring unavailable"
        self._micmon = mon
        log("mic monitoring active")
        self._mic_reselect(mon.last_names)  # startup recovery

    # -- session lock / suspend watch (pause_when_locked) --------------------

    def _start_lockmon(self) -> None:
        """Best-effort lock/suspend watch (the tray/micmon contract): while
        the session is locked or suspended, hotkey entries are ignored and
        an active dictation is cancelled (see lockmon.py for the sources)."""
        if not self.cfg.get("general", {}).get("pause_when_locked", True):
            return  # feature disabled
        from .lockmon import VIA_DISPLAY, LockMonitor
        mon = LockMonitor(on_change=self._on_locked, log=log)
        if not mon.start():
            return  # already logged why (dbus missing / logind absent)
        self._lockmon = mon
        st = mon.status()
        if st.get("mode") == "session" and st.get("session"):
            sid = str(st["session"]).rsplit("/", 1)[-1]
            log(f"lock watch active (session {sid} via "
                f"{VIA_DISPLAY.get(st.get('via'), '?')} - hotkeys pause "
                f"while the screen is locked)")
        else:  # sleep-only: no graphical session, suspend still gates
            log("lock watch active (suspend-only: no graphical session - "
                "hotkeys pause while suspended)")

    def _start_update_checker(self) -> None:
        """Best-effort update check (the tray/micmon contract): one GitHub
        releases check now + daily, one desktop notification per newer
        release, never any self-installation (see fluidvoice/update.py).
        All I/O happens on the checker thread - startup never blocks."""
        try:
            notif_on = self.cfg.get("notifications", {}).get("enabled", True)
            upd_cfg = self.cfg.get("updates", {})

            def _notify(title: str, body: str) -> None:
                ui.notify(title, body, timeout_ms=8000,
                          enabled=notif_on and upd_cfg.get("notify", True))

            checker = update_mod.UpdateChecker(self.cfg, on_notify=_notify,
                                               log=log)
            if checker.start():
                self._update = checker
                log("update check active (daily)")
        except Exception as e:  # noqa: BLE001 - updater must never kill startup
            log(f"WARN update checker unavailable: {e}")

    def _update_status(self) -> dict:
        """The update sub-dict + flat convenience keys for the status
        action (CLI/UI read the flat ones; the dict carries the rest,
        including the copy-paste upgrade_command)."""
        if self._update is not None:
            upd = self._update.status()
        else:
            upd = {"enabled": False, "checked": False, "latest": None,
                   "update_available": None, "url": None, "error": None,
                   "checked_at": None,
                   "method": update_mod.detect_install_method()["method"],
                   "upgrade_command": ""}
        return dict(upd)

    def _on_locked(self, locked: bool) -> None:
        """lockmon callback - transitions only (the monitor dedups). The
        transition logs once here; per-press gating in toggle() stays
        quiet so a locked screen with a stuck hotkey cannot spam."""
        if locked:
            log("screen locked - hotkeys paused")
            with self._lock:
                was_recording = self.recording
            if was_recording:
                self.cancel()  # existing path: watchdog off, discard, notify
            if self._commands.pending:
                self._commands.cancel_pending()
            self._refresh_tray()
        else:
            log("screen unlocked - hotkeys resumed")
            self._refresh_tray()

    def _apply_lock_setting(self) -> None:
        """general.pause_when_locked flipped live: start/stop the monitor.
        Stopping also clears the paused state (hotkeys resume at once)."""
        enabled = bool(self.cfg.get("general", {})
                       .get("pause_when_locked", True))
        if enabled:
            if self._lockmon is None:
                self._start_lockmon()
            return
        if self._lockmon is not None:
            self._lockmon.stop()
            self._lockmon = None
        if self._locked:
            self._locked = False
            log("lock pause disabled - hotkeys resume")
        self._refresh_tray()

    def _on_sources_changed(self, added, removed, current) -> None:
        if added or removed:
            log(f"audio sources: +{', '.join(added) or '—'} "
                f"-{', '.join(removed) or '—'}")
        with self._lock:
            idle = not self.recording and not self.busy
        if not idle:
            return  # mid-dictation safety: retried on the next idle poll
        self._mic_reselect(current)

    def _mic_reselect(self, names: list[str]) -> None:
        """Auto-switch ONLY when the configured device is absent and a
        priority match exists. device == "" (auto) is never touched; a
        working configured device is never upgraded (no preemptive
        switching on connect)."""
        device = self.cfg["recording"].get("device", "")
        if not device:
            return
        if device.endswith(".monitor"):
            # explicit monitor tap (routing/testing): invisible to the
            # monitor-excluding source list BY DESIGN - trust the setting,
            # never warn or auto-switch away from it
            return
        if device in names:
            self._mic_missing_logged = False  # reset the warn-once latch
            return
        patterns = self.cfg["recording"].get("mic_priority") or []
        best = micmon_match_priority(patterns, names)
        if best is None:
            if not self._mic_missing_logged:
                log(f"microphone '{device}' unavailable and no priority match")
                self._mic_missing_logged = True
            return
        self._mic_missing_logged = False
        self._set_device(best)  # existing path: lock re-check, save, rebuild
        ui.notify("SayItErmano", f"Microphone switched to {best}",
                  enabled=self.cfg["notifications"]["enabled"])

    def _quit_gracefully(self) -> None:
        log("quit requested from menu")
        import os
        self._tasks.schedule("quit-signal",
                             lambda: os.kill(os.getpid(), signal.SIGTERM),
                             0.1)

    def _tray_recording(self, recording: bool) -> None:
        if self._tray is not None:
            self._tray.set_recording(recording)
        if self._hotkey is not None:
            try:
                self._hotkey.set_recording(recording)  # Escape grab while up
            except Exception:
                pass
        if self._mouse_ptt is not None:
            try:
                self._mouse_ptt.set_recording(recording)
            except Exception:
                pass

    def _tray_tooltip(self) -> str:
        with self._lock:
            if self.recording:
                state = "Recording… click to stop"
            elif self.busy:
                state = "Processing…"
            else:
                state = "Ready"
            backend_unloaded = self.backend is None
            last_activity = self._engines.last_activity
        hk = self.cfg["hotkey"].get("key", "")
        hint = f" — {hk} or click to dictate" if hk else ""
        tip = f"SayItErmano: {state}{hint}"
        hotkey = self._hotkey  # snapshot: the tray thread reads this too
        if hotkey is not None and not hotkey.hotkey_grabbed:
            tip += " - hotkey blocked!"
        if self._locked:
            tip += " - paused (locked)"
        if backend_unloaded and self._engines.idle_threshold() > 0:
            idle_m = int((time.monotonic() - last_activity) // 60)
            tip += f" - model unloaded (idle {idle_m}m)"
        try:
            lang, source = self._engines.language_detail()
            if source == "cycle" or lang != "auto":
                tip += f" — lang: {lang}"
        except Exception:  # noqa: BLE001 - tooltip must never break the tray
            pass
        return tip

    def _spawn_app(self, *args: str) -> None:
        """Launch the native GTK app in the same interpreter/env as us."""
        import os
        import subprocess
        if os.environ.get("SAYITERMANO_NO_APP_SPAWN"):  # tests/headless
            log(f"app launch suppressed ({' '.join(args) or 'app'})")
            return
        try:
            subprocess.Popen([sys.executable, "-m", "fluidvoice", "app", *args],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log(f"WARN could not launch the app: {e}")

    def _open_settings(self, page: str = "") -> None:
        target = (page or "").strip("/") or "settings"
        self._spawn_app("--open", "history" if target == "history" else "settings")
        log(f"tray: opened {target}")

    def shutdown(self) -> None:
        log("shutting down")
        self._engines.stop_idle_watch()
        # under the lock: the take-state mutations race the hotkey thread's
        # toggle (N1). The race was benign - callbacks re-validate recorder
        # identity - but serializing costs nothing and matches
        # capture.cancel_locked, which already cancels under this lock.
        with self._lock:
            self._capture.abort_locked()
        if self._micmon:
            self._micmon.stop()
            self._micmon = None
        if self._lockmon:
            self._lockmon.stop()
            self._lockmon = None
        if self._update:
            self._update.stop()
            self._update = None
        self._capture.stop_preview()
        self._capture.close_closing_display()
        self.cancel_pending_command()  # pill + Escape grab gone before hotkeys
        if self._tray:
            self._tray.stop()
        if self._hotkey:
            self._hotkey.stop()
        if self._mouse_ptt:
            self._mouse_ptt.stop()
        if self._evdev_ptt:
            self._evdev_ptt.stop()
        if self._rewrite_hotkey:
            self._rewrite_hotkey.stop()
        if self._command_hotkey:
            self._command_hotkey.stop()
        if self._paste_hotkey:
            self._paste_hotkey.stop()
        if self._language_hotkey:
            self._language_hotkey.stop()
        for hk in self._extra_hotkeys:
            hk.stop()
        self._extra_hotkeys = []
        # Supervised timers/threads: pending timers are cancelled and
        # in-flight workers joined, each bounded (see RuntimeTasks.shutdown)
        # - a mid-flight transcription/command gets a short grace window,
        # then the process exits as before.
        report = self._tasks.shutdown(timeout=10.0)
        if report.get("timed_out"):
            log(f"WARN tasks still running after shutdown join: "
                f"{', '.join(report['timed_out'])}")
        if self._srv:
            # ControlServer.shutdown() unlinks the socket path only when
            # it still points at THIS daemon's socket (inode check). A
            # blind unlink here could delete a replacement daemon's
            # freshly bound control channel (F5).
            try:
                self._srv.close()
            except OSError:
                pass

    @staticmethod
    def _sweep_stale_tmp() -> None:
        """Delete SayItErmano temp wavs abandoned by hard crashes (older than 1 day); also sweeps the pre-rename /tmp/fluidvoice-*.wav leftovers."""
        import glob
        import time as _time
        cutoff = _time.time() - 86400
        for pattern in ("/tmp/sayitermano-*.wav", "/tmp/fluidvoice-*.wav"):
            for f in glob.glob(pattern):
                try:
                    if os.path.getmtime(f) < cutoff:
                        os.unlink(f)
                except OSError:
                    pass

    def _start_hotkey(self) -> str | None:
        """(Re-)grab the dictation hotkeys. Returns the first error, if any.
        Wayland sessions skip the X grabs entirely (they cannot work) and
        say so once: the declared wayland hotkey is the DE-shortcut assist."""
        if self._session.is_wayland:
            script = session_mod.ensure_toggle_script()
            log("hotkey: wayland session - global grabs do not exist; bind a "
                f"DE custom shortcut to {script or '`sayit-ermano toggle`'} "
                "(sayit-ermano doctor prints per-DE steps)")
            return None
        from .hotkey import HotkeyError, HotkeyListener
        error = None
        hk = self.cfg["hotkey"]
        try:
            self._hotkey = HotkeyListener(
                key=hk["key"], modifiers=hk.get("modifiers", []),
                mode=hk.get("mode", "toggle"),
                on_toggle=self.toggle,
                on_cancel=self.cancel,
                cancel_key=hk.get("cancel_key", "Escape"),
                log=log,
                on_grab_change=lambda healthy: self._refresh_tray())
            self._hotkey.start()
            for line in self._hotkey.summary:
                log(line)
            self._log_grab_state(self._hotkey, "", hk["key"])
        except HotkeyError as e:
            self._hotkey = None
            log(f"WARN hotkey unavailable: {e}")
            ui.notify("SayItErmano", f"Hotkey unavailable: {e}\n"
                      "Bind a DE shortcut to `sayit-ermano toggle` instead.",
                      timeout_ms=8000, enabled=self.cfg["notifications"]["enabled"])
            error = str(e)
        rewrite_key = (hk.get("rewrite_key") or "").strip()
        if rewrite_key:
            try:
                self._rewrite_hotkey = HotkeyListener(
                    key=rewrite_key, modifiers=[], mode="toggle",
                    on_toggle=self.start_rewrite, log=log)
                self._rewrite_hotkey.start()
                for line in self._rewrite_hotkey.summary:
                    log(line)
                self._log_grab_state(self._rewrite_hotkey, "rewrite ", rewrite_key)
            except HotkeyError as e:
                self._rewrite_hotkey = None
                log(f"WARN rewrite hotkey unavailable: {e}")
                error = error or str(e)
        command_key = (hk.get("command_key") or "").strip()
        if command_key:
            try:
                self._command_hotkey = HotkeyListener(
                    key=command_key, modifiers=[], mode="toggle",
                    on_toggle=self._on_command_hotkey,
                    on_cancel=self.cancel_pending_command,
                    cancel_key=hk.get("cancel_key", "Escape"), log=log)
                self._command_hotkey.start()
                for line in self._command_hotkey.summary:
                    log(line)
                self._log_grab_state(self._command_hotkey, "command ", command_key)
            except HotkeyError as e:
                self._command_hotkey = None
                log(f"WARN command hotkey unavailable: {e}")
                error = error or str(e)
        paste_key = (hk.get("paste_key") or "").strip()
        if paste_key:
            try:
                self._paste_hotkey = HotkeyListener(
                    key=paste_key, modifiers=[], mode="toggle",
                    on_toggle=self._on_paste_hotkey, log=log)
                self._paste_hotkey.start()
                for line in self._paste_hotkey.summary:
                    log(line)
                self._log_grab_state(self._paste_hotkey, "paste ", paste_key)
            except HotkeyError as e:
                self._paste_hotkey = None
                log(f"WARN paste hotkey unavailable: {e}")
                error = error or str(e)
        language_key = (hk.get("language_key") or "").strip()
        if language_key:
            try:
                self._language_hotkey = HotkeyListener(
                    key=language_key, modifiers=[], mode="toggle",
                    on_toggle=lambda *_: self._engines.cycle_language(), log=log)
                self._language_hotkey.start()
                for line in self._language_hotkey.summary:
                    log(line)
                self._log_grab_state(self._language_hotkey, "language ",
                                     language_key)
            except HotkeyError as e:
                self._language_hotkey = None
                log(f"WARN language hotkey unavailable: {e}")
                error = error or str(e)
        for i, spec in enumerate(hk.get("extra_shortcuts") or []):
            label = f"shortcut {i + 2} '{spec.get('key')}'"
            try:
                listener = HotkeyListener(
                    key=spec.get("key", ""),
                    modifiers=spec.get("modifiers", []),
                    mode="toggle",
                    on_toggle=lambda p=spec.get("profile", ""):
                        self._toggle_with_profile(p),
                    log=log)
                listener.start()
                for line in listener.summary:
                    log(line)
                self._log_grab_state(listener, "extra ", spec.get("key", ""))
                self._extra_hotkeys.append(listener)
            except HotkeyError as e:
                log(f"WARN {label} unavailable: {e}")
                error = error or str(e)
        return error

    def _log_grab_state(self, listener, label: str, key: str) -> None:
        """Startup honesty: a refused grab (another client already holds
        the key) must be loud - WARN log + desktop notification - instead
        of the old silent keyless 'ready'. The listener keeps retrying on
        its poll cadence and logs 'hotkey grab recovered' itself."""
        try:
            healthy = listener.hotkey_grabbed
        except Exception:
            return
        if healthy:
            return
        log(f"WARN {label}hotkey '{key}' grab refused - "
            "held by another client, will retry")
        ui.notify("SayItErmano",
                  "Hotkey grab refused — another app holds the key; "
                  "retrying automatically", timeout_ms=8000,
                  enabled=self.cfg["notifications"]["enabled"])

    def _refresh_tray(self) -> None:
        """Push the current state to the tray from the hotkey thread: a
        grab-health flip must reach the tooltip (' - hotkey blocked!')
        without waiting for the next recording transition."""
        tray = self._tray
        if tray is not None:
            try:
                tray.refresh()
            except Exception:
                pass

    def _restart_hotkey(self) -> None:
        """Re-grab the hotkeys after a settings change (frees the old grabs
        first). Raises on failure so the settings UI can surface it."""
        if not self.use_hotkey:
            return
        for attr in ("_hotkey", "_rewrite_hotkey", "_command_hotkey",
                     "_language_hotkey"):
            listener = getattr(self, attr)
            if listener is not None:
                try:
                    listener.stop()
                except Exception as e:
                    log(f"WARN stopping old hotkey grab failed: {e}")
                setattr(self, attr, None)
        error = self._start_hotkey()
        if error:
            raise RuntimeError(f"hotkey re-bind failed: {error} "
                               "(fix the key and save again)")

    def _start_mouse_ptt(self) -> str | None:
        """(Re-)arm mouse push-to-talk (XGrabButton press + XI2 raw
        release detection, see hotkey.MousePTTListener). Empty config =
        off (silent skip). Failures WARN + notify but never kill the
        daemon - the keyboard hotkey keeps working. Returns the first
        error, if any. Wayland: XGrabButton only sees Xwayland apps -
        skipped with a hint at the evdev push-to-talk instead."""
        from .hotkey import HotkeyError, MousePTTListener, parse_button_spec
        spec = (self.cfg["recording"].get("push_to_talk_button") or "").strip()
        try:
            button = parse_button_spec(spec)
        except HotkeyError as e:
            log(f"WARN mouse PTT unavailable: {e}")
            ui.notify("SayItErmano",
                      f"Mouse push-to-talk unavailable: {e}\n"
                      "Fix recording.push_to_talk_button in the config.",
                      timeout_ms=8000,
                      enabled=self.cfg["notifications"]["enabled"])
            return str(e)
        if self._session.is_wayland and button is not None:
            log("mouse push-to-talk: X11-only (XGrabButton sees Xwayland apps "
                "at best) - use hotkey.wayland_evdev on wayland")
            return None
        if button is None:
            return None  # not configured: keyboard hotkey only
        try:
            self._mouse_ptt = MousePTTListener(
                button=button,
                modifiers=self.cfg["recording"].get(
                    "push_to_talk_modifiers", []),
                on_toggle=self.toggle,
                on_cancel=self.cancel,
                cancel_key=self.cfg["hotkey"].get("cancel_key", "Escape"),
                log=log,
                on_grab_change=lambda healthy: self._refresh_tray())
            self._mouse_ptt.start()
            for line in self._mouse_ptt.summary:
                log(line)
            self._log_mouse_grab_state(self._mouse_ptt)
        except HotkeyError as e:
            self._mouse_ptt = None
            log(f"WARN mouse PTT unavailable: {e}")
            ui.notify("SayItErmano", f"Mouse push-to-talk unavailable: {e}",
                      timeout_ms=8000,
                      enabled=self.cfg["notifications"]["enabled"])
            return str(e)
        return None

    def _log_mouse_grab_state(self, listener) -> None:
        """Startup honesty for the button grab (see _log_grab_state): a
        refused combo set is loud instead of a silently dead button."""
        try:
            healthy = listener.button_grabbed
        except Exception:
            return
        if healthy:
            return
        log(f"WARN mouse PTT button {listener.button} grab refused - "
            "held by another client, will retry")
        ui.notify("SayItErmano",
                  "Mouse button grab refused — another app holds it; "
                  "retrying automatically", timeout_ms=8000,
                  enabled=self.cfg["notifications"]["enabled"])

    def _restart_mouse_ptt(self) -> None:
        """Re-arm mouse PTT after a settings change (frees the old grab
        first). Raises on failure so the settings UI can surface it - the
        same contract as _restart_hotkey."""
        if self._mouse_ptt is not None:
            try:
                self._mouse_ptt.stop()
            except Exception as e:
                log(f"WARN stopping old mouse PTT grab failed: {e}")
            self._mouse_ptt = None
        error = self._start_mouse_ptt()
        if error:
            raise RuntimeError(f"mouse push-to-talk re-bind failed: {error} "
                               "(fix the button and save again)")

    # -- wayland evdev push-to-talk (hotkey.wayland_evdev) --------------------

    def _start_evdev_ptt(self) -> str | None:
        """Optional physical push-to-talk on wayland: hold a key read from
        /dev/input (privileged path - input group + python-evdev). Off by
        default; any failure WARNs and leaves the daemon fully useful.
        Returns the first error, if any."""
        hk = self.cfg["hotkey"]
        if not self._session.is_wayland or not hk.get("wayland_evdev", False):
            return None
        from .evdev_ptt import EvdevPTT
        try:
            ptt = EvdevPTT(
                device_substr=str(hk.get("wayland_evdev_device", "")),
                key_name=str(hk.get("wayland_evdev_key", "KEY_RIGHTCTRL")),
                on_press=self._evdev_toggle,
                on_release=self._evdev_toggle,
                log=log)
            if not ptt.start():
                return "evdev push-to-talk unavailable (see the daemon log)"
            self._evdev_ptt = ptt
            for line in ptt.summary:
                log(line)
            return None
        except Exception as e:  # noqa: BLE001 - never fatal (the plan)
            self._evdev_ptt = None
            log(f"WARN evdev push-to-talk unavailable: {e}")
            return str(e)

    def _evdev_toggle(self) -> None:
        """Both edges of the hold map to toggle() (press starts, release
        stops + transcribes) - the daemon's own state machine keeps it
        idempotent."""
        self.toggle()

    def _restart_evdev_ptt(self) -> None:
        """Re-arm after a settings change (the hotkey.* restart hook)."""
        if self._evdev_ptt is not None:
            try:
                self._evdev_ptt.stop()
            except Exception as e:
                log(f"WARN stopping old evdev push-to-talk failed: {e}")
            self._evdev_ptt = None
        error = self._start_evdev_ptt()
        if error:
            raise RuntimeError(f"evdev push-to-talk re-bind failed: {error} "
                               "(check the device pattern and save again)")

    def _rebuild_recorder(self) -> None:
        """New Recorder from the current cfg (command/device changed)."""
        rcfg = self.cfg["recording"]
        self.recorder = Recorder(command=rcfg.get("command", "auto"),
                                 device=rcfg.get("device", ""),
                                 sample_rate=rcfg.get("sample_rate", 16000))

    def _apply_tray_setting(self) -> None:
        enabled = self.cfg["general"].get("tray_enabled", True)
        if not enabled and self._tray is not None:
            self._tray.stop()
            self._tray = None
            log("tray disabled (settings change)")
        elif enabled and self._tray is None:
            self._start_tray()

    def apply_config(self, changed: list[str]) -> dict:
        """Apply just-saved settings that need more than a cfg-dict update
        (everything else is read from cfg at use time, or needs a daemon
        restart - see config.RESTART_REQUIRED). Feedback for the settings UI."""
        applied: list[str] = []
        errors: list[str] = []

        def _try(area: str, fn) -> None:
            try:
                fn()
                applied.append(area)
            except Exception as e:  # noqa: BLE001 - surfaced in the UI
                errors.append(f"{area}: {e}")

        if any(k.startswith("hotkey.") for k in changed):
            _try("hotkeys", self._restart_hotkey)
            if self._session.is_wayland:
                _try("evdev push-to-talk", self._restart_evdev_ptt)
        if any(k in ("recording.push_to_talk_button",
                     "recording.push_to_talk_modifiers") for k in changed):
            _try("mouse push-to-talk", self._restart_mouse_ptt)
        if "recording.command" in changed:
            _try("recorder", self._rebuild_recorder)
        if "recording.device" in changed:
            def _set_mic() -> None:
                with self._lock:
                    if self.recording or self.busy:
                        raise RuntimeError("cannot switch microphone while dictating")
                self._set_device(self.cfg["recording"].get("device", ""))
            _try("microphone", _set_mic)
        if "general.tray_enabled" in changed:
            _try("tray", self._apply_tray_setting)
        if "general.pause_when_locked" in changed:
            _try("lock pause", self._apply_lock_setting)
        if "model.idle_unload_s" in changed:
            _try("idle unload", self._engines.apply_idle_unload_setting)
        return {"applied": applied, "errors": errors}

    # -- control protocol ----------------------------------------------------

    def handle_request(self, req: dict) -> dict:
        action = req.get("action")
        if action == "toggle":
            recording = self.toggle()
            return {"ok": True, "recording": recording}
        if action == "cancel":
            self.cancel()
            return {"ok": True, "recording": False, "cancelled": True}
        if action == "paste-last":
            ok, detail = self.paste_last()
            return {"ok": ok, "error": detail if not ok else None}
        if action == "cycle-language":
            return {"ok": True, **self._engines.cycle_language()}
        if action == "insert-text":
            ok, detail = self.insert_text_action(str(req.get("text", "")))
            return {"ok": ok, "error": detail if not ok else None}
        if action == "command-rerun":
            purpose = req.get("purpose")
            return self._commands.rerun(str(req.get("command", "")),
                                       str(purpose) if purpose else None)
        if action == "status":
            upd = self._update_status()
            with self._lock:
                model_state = {
                    "policy_s": self._engines.idle_threshold(),
                    "loaded": self.backend is not None,
                    "idle_s": round(time.monotonic()
                                     - self._engines.last_activity, 1),
                }
            return {"ok": True, "recording": self.recording, "busy": self.busy,
                    "backend": self.backend.name if self.backend else None,
                    # what the model ACTUALLY runs on: the loaded backend's
                    # resolved device (post auto-pick and CPU fallback);
                    # with no model loaded, what "auto" would pick
                    "cuda": (getattr(self.backend, "device", "") == "cuda"
                             if self.backend is not None else
                             backends.cuda_available()),
                    "version": __version__,
                    # None = hotkey disabled/--no-hotkey; False = every
                    # lock-mask combo not held (blocked, daemon retrying)
                    "hotkey_grabbed": (self._hotkey.hotkey_grabbed
                                       if self._hotkey is not None else None),
                    # None = no button configured (or unavailable); False =
                    # the button grab is refused and being retried
                    "mouse_ptt_grabbed": (self._mouse_ptt.button_grabbed
                                          if self._mouse_ptt is not None
                                          else None),
                    "locked": self._locked,
                    # lock watch surface (lockmon status: mode/via name the
                    # watched session - the doctor lock line reads this)
                    "lock_watch": (self._lockmon.status()
                                   if self._lockmon is not None else
                                   {"active": False, "mode": "off",
                                    "session": None, "via": None,
                                    "locked": self._locked}),
                    # session type + per-capability backends (wayland port
                    # v0.3; additive keys - JSON consumers unaffected)
                    "session": {"type": self._session.type,
                                "desktop": self._session.desktop},
                    "capabilities": session_mod.capabilities(
                        self._session, cfg=self.cfg),
                    "warmup": dict(self.warmup),
                    "active_model": self._engines.active_model_name(),
                    "active_model_key": backends.backend_model_key(self.backend)
                                       or backends.config_model_key(self.cfg),
                    "today": history_mod.today_stats(history_mod.read_all()),
                    # update check-and-assist (fluidvoice/update.py): the
                    # dict carries everything; the two flat keys are the
                    # CLI/UI convenience surface
                    "update": upd,
                    "update_available": upd.get("update_available"),
                    "update_url": upd.get("url"),
                    # idle-unload policy + live state (doctor/tray read
                    # this; additive key - JSON consumers unaffected)
                    "model_state": model_state,
                    # language cycle + guard state (doctor/CLI/GTK read
                    # this; additive key - JSON consumers unaffected)
                    "language": self._engines.language_status()}
        if action == "shutdown":
            self._quit_gracefully()
            return {"ok": True}
        if action == "set-device":
            device = str(req.get("device", ""))
            self._set_device(device)
            return {"ok": True, "device": device}
        if action == "test-dictation":
            return self.test_dictation(float(req.get("seconds", 3.0)))
        if action == "get-config":
            from .config import mask_secrets
            return {"ok": True, "config": mask_secrets(self.cfg)}
        if action == "set-config":
            return self._set_config(req.get("config") or {})
        if action == "select-model":
            return self._engines.select_model(str(req.get("name", "")))
        if action == "model-delete":
            return self._engines.delete_model(str(req.get("kind", "")),
                                     str(req.get("name", "")))
        if action == "mics":
            from .tray import list_microphones
            return {"ok": True, "mics": list_microphones()}
        if action == "transcribe":
            return self._api_transcribe(str(req.get("path") or ""),
                                        bool(req.get("process", False)))
        if action == "history":
            return self._api_history(req)
        return {"ok": False, "error": f"unknown action {action!r}"}

    def _set_config(self, body: dict) -> dict:
        """Validated settings merge over the socket (native-app spec):
        validate -> save -> hot-apply what the daemon can take live."""
        from .config import ENGINE_KEYS, RESTART_REQUIRED, apply_settings, save_config
        changed, rejected = apply_settings(self.cfg, body)
        if changed:
            try:
                save_config(self.cfg)
            except Exception as e:
                return {"ok": False, "error": f"save failed: {e}",
                        "changed": [], "rejected": rejected}
        restart = [k for k in changed if k in RESTART_REQUIRED]
        live = [k for k in changed if k not in RESTART_REQUIRED]
        applied: list[str] = []
        errors: list[str] = []
        if live:
            feedback = self.apply_config(live)
            applied = feedback.get("applied", [])
            errors = feedback.get("errors", [])
        if any(k in ENGINE_KEYS for k in changed):
            if self._engines.start_engine_reload():
                applied.append("speech engine (reloading)")
            else:
                errors.append("speech engine: a load is already running - "
                              "save again once it finishes")
        note = ("restart the daemon to apply: " + ", ".join(restart)) \
            if restart else ""
        return {"ok": not rejected, "changed": changed, "rejected": rejected,
                "restart_required": restart, "applied": applied,
                "errors": errors, "note": note}

    # -- dictation -----------------------------------------------------------

    def start_rewrite(self) -> None:
        """Rewrite hotkey: capture the selection, then record the instruction."""
        if self._locked:
            return  # session locked: hotkey entries ignored
        from . import rewrite as rewrite_mod
        with self._lock:
            if self.recording or self.busy:
                return
            try:
                context = rewrite_mod.capture_selection()
            except Exception as e:
                log(f"selection capture failed: {e}")
                context = ""
            log(f"rewrite mode (context: {len(context or '')} chars)")
            self._capture.start_locked(mode="rewrite",
                                       rewrite_context=context or None)

    def start_command(self) -> None:
        """Command hotkey: start recording the spoken instruction."""
        if self._locked:
            return  # session locked: hotkey entries ignored
        from . import command as command_mod
        with self._lock:
            if self.recording or self.busy or self._commands.pending:
                return
        ready = command_mod.command_mode_ready(self.cfg)
        if ready:
            log(ready)
            ui.notify("SayItErmano", f"Command mode unavailable: {ready}",
                      enabled=self.cfg["notifications"]["enabled"])
            return
        with self._lock:
            log("command mode")
            self._capture.start_locked(mode="command")

    def _on_command_hotkey(self) -> None:
        """Command hotkey router: a second press CONFIRMS a pending proposal
        (the only execution trigger); otherwise it starts a recording."""
        if self._commands.pending:
            self._commands.confirm_pending()
        else:
            self.start_command()  # guards make this a no-op mid-recording

    def cancel_pending_command(self) -> None:
        """Escape/shutdown/lock on a pending proposal: nothing executes."""
        self._commands.cancel_pending()

    def toggle(self) -> bool:
        if self._locked:
            # session locked: ignore the press quietly (the transition was
            # logged once; do not swallow into a recording state)
            return False
        with self._lock:
            if self.recording:
                self._capture.stop_locked()
                return False
            if self.busy:
                log("still processing previous dictation; ignoring toggle")
                return False
            self._capture.start_locked()
            return self.recording

    def cancel(self) -> None:
        """Escape/socket cancel: discard the take (CaptureCoordinator)."""
        self._capture.cancel()

    def _on_take_start(self) -> None:
        """Capture hook: a take started while no backend is loaded - spawn
        the background reload (the model loads while the user speaks)."""
        if self._engines.backend is None:
            self._tasks.spawn("reload", self._engines.reload_backend_bg)

    def _on_take_complete(self, wav: Path, app_hint: str | None,
                          mode: str,
                          rewrite_context: str | None) -> None:
        """Capture hook: a finished take - spawn the processing thread."""
        t = self._tasks.prepare(
            "process", self._process,
            args=(wav, app_hint, mode, rewrite_context))
        self._process_thread = t
        if t is not None:
            t.start()

    def _maybe_first_run_onboard(self) -> None:
        """Open onboarding once on first launch (macOS parity: the app opens
        onboarding before the first dictation)."""
        try:
            marker = paths.data_dir() / ".onboarded"
            if marker.exists() or history_mod.tail(1):
                return
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("opened\n")  # once only, even if skipped
            self._spawn_app("--onboard")
            log("first run detected - opened the setup page")
        except Exception as e:
            log(f"WARN onboarding open failed: {e}")

    # -- scriptable unix-socket API (C1) --------------------------------------

    _API_MAX_BYTES = 200 * 1024 * 1024  # v1 is not chunked; reject, don't OOM

    def _api_transcribe(self, raw_path: str, process: bool) -> dict:
        """`transcribe {path, process?}`: a file -> text through the warm
        daemon backend (no second model load). Refuses while a take is
        running or the pipeline is busy - GPU work must not interleave."""
        import shutil as _shutil

        from .audio_utils import AudioFormatError, ensure_wav
        path = Path(raw_path).expanduser()
        if not raw_path:
            return {"ok": False, "error": "path is required"}
        if not path.is_file():
            return {"ok": False, "error": f"file not found: {path}"}
        size = path.stat().st_size
        if size > self._API_MAX_BYTES:
            return {"ok": False,
                    "error": f"file too large ({size / 1e6:.0f} MB; v1 is "
                             "not chunked - shrink or split it first)"}
        with self._lock:
            if self.recording:
                return {"ok": False, "error": "busy (recording)"}
            if self.busy:
                return {"ok": False, "error": "busy"}
            self.busy = True
        try:
            audio, converted_dir = path, None
            try:
                audio = ensure_wav(
                    path, force=getattr(self.backend, "name", "")
                    == "whisper.cpp")
            except AudioFormatError as e:
                return {"ok": False, "error": str(e)}
            if audio != path:
                converted_dir = audio.parent
            try:
                result = Transcript.of(self._engines.ensure_backend().transcribe(
                    audio, self._engines.language_detail()[0]) or {})
            finally:
                if converted_dir is not None:
                    _shutil.rmtree(converted_dir, ignore_errors=True)
            text = result.text
            if process:
                text = post_process(text, self.cfg)
            # control-edge serialization: the historical response shape
            # (text/language/duration_s), byte-identical to pre-seam
            return {"ok": True, "path": str(path), "text": text,
                    "language": result.language,
                    "duration_s": result.duration}
        except Exception as e:  # noqa: BLE001 - API errors are payloads
            return {"ok": False, "error": str(e)}
        finally:
            with self._lock:
                self.busy = False

    def _api_history(self, req: dict) -> dict:
        """`history {limit?, since_ts?}`: recent stored rows verbatim."""
        try:
            limit = max(1, min(200, int(req.get("limit", 10))))
        except (TypeError, ValueError):
            return {"ok": False, "error": "limit must be an integer"}
        since = req.get("since_ts")
        try:
            since = float(since) if since is not None else None
        except (TypeError, ValueError):
            return {"ok": False, "error": "since_ts must be a number"}
        if since is not None:
            # the file is capped and small; read_all keeps since_ts honest
            # where tail()'s 128 KB window could silently truncate
            entries = [e for e in history_mod.read_all()
                       if float(e.get("ts") or 0) >= since]
        else:
            entries = history_mod.tail(limit)
        entries = entries[-limit:]
        return {"ok": True, "count": len(entries), "entries": entries}

    def test_dictation(self, seconds: float = 3.0) -> dict:
        """Onboarding tryout (upstream's real-dictation step): record a few
        seconds and transcribe them WITHOUT typing anywhere."""
        with self._lock:
            if self.recording:
                return {"ok": False, "error": "currently recording"}
            if self.busy:
                return {"ok": False, "error": "busy"}
            self.busy = True
        fd, tmp = tempfile.mkstemp(prefix="sayitermano-onboard-", suffix=".wav")
        os.close(fd)
        try:
            try:
                self.recorder.start(Path(tmp))
            except RecorderError as e:
                return {"ok": False, "error": f"recorder error: {e}"}
            time.sleep(max(1.0, min(seconds, 8.0)))
            wav = self.recorder.stop()
            if wav is None or not Path(wav).exists() \
                    or Path(wav).stat().st_size < 200:
                return {"ok": False,
                        "error": "no audio captured (muted or wrong mic?)"}
            duration = duration_seconds(Path(wav))
            if is_silent(Path(wav)):
                return {"ok": False, "duration_s": duration,
                        "error": "audio was silent - is the mic muted?"}
            backend = self._engines.ensure_backend()
            result = Transcript.of(backend.transcribe(
                Path(wav), self._engines.language_detail()[0]) or {})
            return {"ok": True, "duration_s": round(duration, 1),
                    "text": result.text}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            Path(tmp).unlink(missing_ok=True)
            with self._lock:
                self.busy = False

    def _toggle_with_profile(self, profile: str) -> None:
        """Extra-shortcut toggle (B1): a take STARTED by a profiled
        shortcut polishes with that named prompt profile (upstream
        per-shortcut AI-prompt picker). The override clears when the
        take finishes or is cancelled."""
        if not self.recording:
            self._capture.profile_override = profile or None
            if self._capture.profile_override:
                log(f"take starts with prompt profile "
                    f"'{self._capture.profile_override}'")
        self.toggle()

    def _on_paste_hotkey(self) -> None:
        ok, detail = self.paste_last()
        if not ok and detail:
            ui.notify("SayItErmano", f"Paste last: {detail}",
                      enabled=self.cfg["notifications"]["enabled"])

    def paste_last(self) -> tuple[bool, str | None]:
        """Re-type the most recent transcription (upstream paste-last hotkey)."""
        if self.busy or self.recording:
            return False, "busy"
        text = (self.last_result or {}).get("text") or ""
        if not text:
            from . import history as history_mod
            entries = history_mod.tail(1)
            text = entries[0].get("text", "") if entries else ""
        if not text:
            return False, "nothing to paste"
        try:
            insertion.insert_text(text, self.cfg)
            log(f"pasted last transcription ({len(text)} chars)")
            return True, None
        except insertion.InsertError as e:
            log(f"paste-last failed: {e}")
            return False, str(e)

    def insert_text_action(self, text: str) -> tuple[bool, str | None]:
        """Type arbitrary text into the focused app (history-window repair
        path: edit a transcript, then re-insert it)."""
        if self.busy or self.recording:
            return False, "busy"
        text = (text or "").strip()
        if not text:
            return False, "nothing to insert"
        try:
            insertion.insert_text(text, self.cfg)
            log(f"inserted text from history ({len(text)} chars)")
            return True, None
        except insertion.InsertError as e:
            log(f"insert-text failed: {e}")
            return False, str(e)

    # -- pipeline ------------------------------------------------------------

    def _process(self, wav: Path, app_hint: str | None,
                 mode: str = "dictate", rewrite_context: str | None = None) -> None:
        self.busy = True
        out: dict = {}
        try:
            try:
                backend = self._engines.ensure_backend()
            except Exception as e:
                log(f"transcription failed: {e}")
                ui.notify("SayItErmano", f"Transcription failed: {e}",
                          enabled=self.cfg["notifications"]["enabled"])
                wav.unlink(missing_ok=True)
                return
            pipeline = self._pipeline_factory(self.cfg, backend)
            pipeline._profile_override = self._capture.profile_override
            # sticky runtime cycle override (unlike the profile override,
            # deliberately NOT cleared in the finally below: cycle state
            # persists until cycled away or the daemon restarts)
            pipeline._language_override = self._engines.cycle_runtime()
            out = pipeline.run(wav, app_hint, mode=mode,
                               rewrite_context=rewrite_context) or {}
            self.last_result = out
        finally:
            self.busy = False
            self._engines.touch_activity()
            self._capture.profile_override = None
            display = self._capture.take_closing_display()
            if display is not None:
                if mode == "command":
                    display.close()  # the panel takes over the conversation
                elif out.get("text"):
                    # peak-end done beat: the pill shows the success frame,
                    # then fades itself (research §7); low confidence tints
                    # the badge amber (research §5)
                    display.finish("✓ AI" if out.get("ai") else "✓",
                                   confidence=out.get("confidence"))
                else:
                    display.close()
        # Turn 1 runs AFTER busy clears, so there is no busy-flag race with
        # the hotkey-confirm handoff below.
        if mode == "command" and out.get("mode") == "command":
            self._commands.begin(str(out.get("text", "")), app=app_hint)
