"""The SayItErmano daemon: hotkey -> record -> transcribe -> polish -> type.

Structure:
  Daemon            state machine (idle/recording/busy), hotkey + socket wiring
  DictationPipeline one utterance: wav -> transcribe -> post-process -> optional
                    AI polish -> insert -> history (lives in fluidvoice/
                    pipeline.py since audit C5a; re-exported below). Every
                    step is injectable so the whole flow is unit-testable
                    without audio, X11 or GPU.
"""
from __future__ import annotations

import gc
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
from .config import load_config
from .media import MediaController
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
        self._command_context = None  # lazily built CommandContextStore
        self.backend: Any = None  # lazy: loads model on first use
        self.recording = False
        self.busy = False  # transcription/insertion in flight
        self.last_result: dict = {}
        self._app_hint: str | None = None
        self._rewrite_context: str | None = None
        self._rewrite_mode = False
        # Command mode: recording flag + the awaiting-confirmation state
        self._command_mode = False
        self._command_session = None
        self._command_pending = False
        self._command_destructive_armed = False  # 1st press of strong confirm
        self._command_display = None
        self._command_entries: list[dict] = []  # panel conversation feed
        # RuntimeTasks: every timer/thread the daemon spawns is named,
        # exception-reported and joined at shutdown (fluidvoice/
        # runtime_tasks.py; plan P1.2). The handles below stay Timer/
        # Thread objects so call sites and tests keep identity checks.
        self._tasks = RuntimeTasks(on_exception=self._on_task_exception)
        self._command_timer: threading.Timer | None = None
        self._command_hotkey = None
        self._paste_hotkey = None
        self._language_hotkey = None
        self._extra_hotkeys: list = []
        self._profile_override: str | None = None
        # language cycle: RUNTIME daemon state (hotkey.language_key steps
        # general.language_cycle); None = not engaged, never persisted
        self._cycle_index: int | None = None
        self._watchdog: threading.Timer | None = None
        self._first_pcm_timer: threading.Timer | None = None
        self._preview: Any = None
        self._send_countdown_timer: threading.Timer | None = None
        self._closing_display: Any = None
        self._tray: Any = None
        # -- idle model unload (model.idle_unload_s; 0 = off, byte-identical
        # to the pre-policy behavior) -------------------------------------
        self._last_activity = time.monotonic()  # last take end / warmup
        self._idle_unloaded_at: float | None = None
        self._backend_load_lock = threading.Lock()  # serializes _ensure_backend
        self._idle_thread: threading.Thread | None = None  # watcher when on
        self._idle_stop = threading.Event()
        self._start_warm_thread: threading.Thread | None = None  # run()'s warmup
        self._media = MediaController(log=log)
        self._micmon: Any = None  # input-device watcher (micmon.MicMonitor)
        self._mic_missing_logged = False  # warn-once latch for reselects
        self.warmup: dict = {"running": False, "error": None, "model": None}
        self._warmup_lock = threading.Lock()
        self._lock = threading.Lock()
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

    def _on_task_exception(self, name: str, exc: BaseException) -> None:
        """RuntimeTasks exception reporter: a failed supervised callback
        is logged in full, never raised unhandled in its worker thread
        (which the suite's thread-exception gate turns into a failure)."""
        log(f"WARN runtime task {name!r} failed: "
            f"{exc.__class__.__name__}: {exc}\n"
            + "".join(traceback.format_exception(exc)).rstrip())

    # -- lifecycle -----------------------------------------------------------

    def run(self) -> None:
        log(f"SayItErmano v{__version__} starting")
        self._session = session_mod.probe()
        self._log_session()
        self._sweep_stale_tmp()
        try:
            self.backend = self._backend_factory(self.cfg)
        except Exception as e:
            log(f"WARN speech backend not ready yet ({e}); will retry on first use")
            self.backend = None
        else:
            if not self.cfg["model"].get("eager_warmup", True):
                pass  # warmup disabled (e.g. test isolation on small GPUs)
            else:
                # Load the model in the background so the live preview works
                # from the very first dictation (lazy otherwise). NOTE: the
                # snapshot lives INSIDE _warm (its frame dies with the
                # thread) - a run()-level local would pin the backend for
                # the daemon's whole lifetime and defeat idle unload.
                def _warm():
                    ref = self.backend
                    if ref is None:
                        return  # dropped before the thread ran; nothing to do
                    try:
                        ref.warmup()
                        log("speech model loaded (preview ready)")
                        self._touch_activity()
                    except Exception as e:
                        log(f"WARN model warmup failed: {e}")

                t = self._tasks.prepare("warmup", _warm)
                self._start_warm_thread = t
                if t is not None:
                    t.start()
        # Idle unload watcher: only exists when model.idle_unload_s > 0.
        self._start_idle_watch()

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
            if self._command_pending:
                self.cancel_pending_command()
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
            last_activity = self._last_activity
        hk = self.cfg["hotkey"].get("key", "")
        hint = f" — {hk} or click to dictate" if hk else ""
        tip = f"SayItErmano: {state}{hint}"
        hotkey = self._hotkey  # snapshot: the tray thread reads this too
        if hotkey is not None and not hotkey.hotkey_grabbed:
            tip += " - hotkey blocked!"
        if self._locked:
            tip += " - paused (locked)"
        if backend_unloaded and self._idle_threshold() > 0:
            idle_m = int((time.monotonic() - last_activity) // 60)
            tip += f" - model unloaded (idle {idle_m}m)"
        try:
            lang, source = self._language_detail()
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
        self._stop_idle_watch()
        if self.recording:
            self.recorder.cancel()
            self.recording = False
        if self._watchdog:
            self._watchdog.cancel()
        if self._first_pcm_timer:
            self._first_pcm_timer.cancel()
            self._first_pcm_timer = None
        if self._micmon:
            self._micmon.stop()
            self._micmon = None
        if self._lockmon:
            self._lockmon.stop()
            self._lockmon = None
        if self._update:
            self._update.stop()
            self._update = None
        self._stop_preview()
        self._close_closing_display()
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
            try:
                self._srv.close()
            except OSError:
                pass
            paths.socket_path().unlink(missing_ok=True)

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
                    on_toggle=lambda *_: self._cycle_language(), log=log)
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
            _try("idle unload", self._apply_idle_unload_setting)
        return {"applied": applied, "errors": errors}

    # -- model warmup / hot-swap (native-app spec: select-model over socket) ----

    def _active_model_name(self) -> str:
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
                return {"ok": False, "error": "a model download is already running"}
            self.warmup = {"running": True, "error": None, "model": name}
        self._tasks.spawn("model-switch", self._warmup_model,
                          args=(name,))
        return {"ok": True, "model": name}

    def _warmup_model(self, name: str) -> None:
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
            self._idle_unloaded_at = None
            self._touch_activity()
            self.warmup = {"running": False, "error": None, "model": name}
            log(f"model switched to {name} (hot-swapped)")
        except Exception as e:  # noqa: BLE001 - surfaced in the UI
            self.cfg["model"]["name"] = previous  # rollback, keep usable
            self.warmup = {"running": False, "error": str(e)[:300], "model": name}
            log(f"model switch to {name} failed: {e}")

    def _reload_backend(self) -> None:
        """Rebuild the loaded backend after engine-option changes. Config
        stays saved even on failure - the running backend keeps its last
        working state until the next try."""
        model = self._active_model_name()
        try:
            backend = backends.load_backend(self.cfg)
            backend.warmup()
            self.backend = backend
            self._idle_unloaded_at = None
            self._touch_activity()
            self.warmup = {"running": False, "error": None, "model": model}
        except Exception as e:  # noqa: BLE001 - surfaced in the UI
            self.warmup = {"running": False, "error": str(e)[:300], "model": model}

    # -- idle model unload (model.idle_unload_s) ------------------------------

    def _idle_threshold(self) -> int:
        """Current policy in seconds; 0 = never unload."""
        try:
            return int((self.cfg.get("model", {}) or {})
                       .get("idle_unload_s", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _touch_activity(self) -> None:
        """Record dictation activity (resets the idle clock). Called from
        take start/stop, transcription end, successful warmups and policy
        changes - deliberately NOT from status/preview/history reads."""
        self._last_activity = time.monotonic()

    def _maybe_idle_unload(self, now: float | None = None) -> None:
        """Drop the loaded backend once no dictation activity happened for
        model.idle_unload_s seconds. `now` is injectable for tests. Never
        fires while recording/busy/warming up; the drop itself happens
        outside self._lock (backend destructors can take ~100 ms), but
        inside _backend_load_lock so a load can never land mid-drop."""
        if now is None:
            now = time.monotonic()
        t = self._idle_threshold()
        if t <= 0:
            return
        with self._backend_load_lock:
            with self._lock:
                if self.backend is None or self.recording or self.busy:
                    return
                if self.warmup.get("running"):
                    return  # a model switch/download is in flight
                warm = self._start_warm_thread
                if warm is not None and warm.is_alive():
                    return  # eager startup warmup still running
                idle_s = now - self._last_activity
                if idle_s < t:
                    return
                backend = self.backend
                self.backend = None
                self._idle_unloaded_at = now
            try:
                backend.close()
            except Exception:  # noqa: BLE001 - teardown is best-effort
                pass
            del backend  # release before gc so cycles die here too
            gc.collect()
        log(f"model idle {int(idle_s // 60)}m >= {t}s - unloaded "
            "(reloads on the next dictation)")
        self._refresh_tray()

    def _idle_watch_loop(self) -> None:
        while not self._idle_stop.wait(
                max(5.0, min(60.0, self._idle_threshold() / 4.0))):
            try:
                self._maybe_idle_unload()
            except Exception as e:  # noqa: BLE001 - the watcher must survive
                log(f"WARN idle-unload check failed: {e}")

    def _start_idle_watch(self) -> None:
        """Start the watcher thread iff the policy is on. With
        idle_unload_s = 0 nothing runs - behavior is identical to the
        pre-policy daemon."""
        if self._idle_threshold() <= 0:
            self._idle_thread = None
            return
        if self._idle_thread is not None and self._idle_thread.is_alive():
            return
        self._idle_stop = threading.Event()
        t = self._tasks.prepare("idle-unload", self._idle_watch_loop)
        self._idle_thread = t
        if t is not None:
            t.start()

    def _stop_idle_watch(self) -> None:
        self._idle_stop.set()
        self._tasks.join("idle-unload", timeout=2)

    def _apply_idle_unload_setting(self) -> None:
        """model.idle_unload_s flipped live: restart (or stop) the watcher.
        Setting the policy counts as activity, so a just-enabled window
        never fires immediately."""
        self._stop_idle_watch()
        self._touch_activity()
        self._start_idle_watch()

    def _reload_backend_bg(self) -> None:
        """Take-start reload after an idle unload (or a failed startup
        load): the model loads while the user speaks; _process's
        synchronous _ensure_backend remains the guaranteed - and
        gracefully failing - path."""
        try:
            self._ensure_backend()
        except Exception as e:  # noqa: BLE001 - logged, retried at stop
            log(f"WARN background model reload failed: {e}")

    def delete_model(self, kind: str, name: str) -> dict:
        """Remove one cached model (Settings → Models pruning). The target
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
        log(f"deleted cached model {kind} {name} "
            f"(freed {model_catalog.human_bytes(freed)}): {t}")
        return {"ok": True, "path": str(t), "bytes": freed}

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
            return {"ok": True, **self._cycle_language()}
        if action == "insert-text":
            ok, detail = self.insert_text_action(str(req.get("text", "")))
            return {"ok": ok, "error": detail if not ok else None}
        if action == "command-rerun":
            purpose = req.get("purpose")
            return self._rerun_command(str(req.get("command", "")),
                                       str(purpose) if purpose else None)
        if action == "status":
            upd = self._update_status()
            with self._lock:
                model_state = {
                    "policy_s": self._idle_threshold(),
                    "loaded": self.backend is not None,
                    "idle_s": round(time.monotonic() - self._last_activity, 1),
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
                    "active_model": self._active_model_name(),
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
                    "language": self._language_status()}
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
            return self.select_model(str(req.get("name", "")))
        if action == "model-delete":
            return self.delete_model(str(req.get("kind", "")),
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
            with self._warmup_lock:
                if not self.warmup["running"]:
                    self.warmup = {"running": True, "error": None,
                                   "model": self._active_model_name()}
                    self._tasks.spawn("engine-reload",
                                      self._reload_backend)
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
            self._rewrite_mode = True
            self._rewrite_context = context or None
            log(f"rewrite mode (context: {len(context or '')} chars)")
            self._start_recording_locked()

    def start_command(self) -> None:
        """Command hotkey: start recording the spoken instruction."""
        if self._locked:
            return  # session locked: hotkey entries ignored
        from . import command as command_mod
        with self._lock:
            if self.recording or self.busy or self._command_pending:
                return
        ready = command_mod.command_mode_ready(self.cfg)
        if ready:
            log(ready)
            ui.notify("SayItErmano", f"Command mode unavailable: {ready}",
                      enabled=self.cfg["notifications"]["enabled"])
            return
        with self._lock:
            self._command_mode = True
            log("command mode")
            self._start_recording_locked()

    def _on_command_hotkey(self) -> None:
        """Command hotkey router: a second press CONFIRMS a pending proposal
        (the only execution trigger); otherwise it starts a recording."""
        if self._command_pending:
            self._confirm_pending_command()
        else:
            self.start_command()  # guards make this a no-op mid-recording

    def toggle(self) -> bool:
        if self._locked:
            # session locked: ignore the press quietly (the transition was
            # logged once; do not swallow into a recording state)
            return False
        with self._lock:
            if self.recording:
                self._stop_recording_locked()
                return False
            if self.busy:
                log("still processing previous dictation; ignoring toggle")
                return False
            self._start_recording_locked()
            return self.recording

    def _start_recording_locked(self) -> None:
        self._app_hint = insertion.active_window_class()
        fd, tmp = tempfile.mkstemp(prefix="sayitermano-", suffix=".wav")
        os.close(fd)
        try:
            self.recorder.start(Path(tmp))
        except RecorderError as e:
            log(f"recorder error: {e}")
            ui.notify("SayItErmano", f"Recording failed: {e}",
                      enabled=self.cfg["notifications"]["enabled"])
            Path(tmp).unlink(missing_ok=True)
            return
        self.recording = True
        self._touch_activity()
        if self.backend is None:
            # Take-start reload (after an idle unload, or a failed startup
            # load): the model loads while the user speaks.
            self._tasks.spawn("reload", self._reload_backend_bg)
        self._tray_recording(True)
        if self.cfg["recording"].get("pause_media", True):
            self._media.pause_if_playing()  # upstream: only what's playing
        ui.play_sound("start", self.cfg["sounds"]["volume"],
                      self.use_sounds and self.cfg["sounds"]["enabled"])
        log(f"recording (app={self._app_hint or '?'})")
        max_s = float(self.cfg["recording"].get("max_seconds", 300))
        t = self._tasks.prepare_timer("watchdog", self._auto_stop, max_s)
        self._watchdog = t
        if t is not None:
            t.start()
        # Upstream firstPCMTimeout (2s): a live-but-silent source (muted mic,
        # wrong device, Bluetooth glitch) should fail fast, not record air
        # until max_seconds.
        self._start_preview(getattr(self.recorder, "raw_path", None))
        pcm_timeout = float(self.cfg["recording"].get("first_pcm_timeout", 2.0))
        if pcm_timeout > 0:
            # tracked + cancelled by every take-end path (stop/cancel/
            # shutdown): the callback re-validates identity anyway, but a
            # cancelled timer is the guarantee it never even fires stale
            self._cancel_first_pcm_timer_locked()
            t = self._tasks.prepare_timer(
                "first-pcm", self._check_first_pcm, pcm_timeout,
                args=(self.recorder, Path(tmp)))
            self._first_pcm_timer = t
            if t is not None:
                t.start()
        # mid-take stall watchdog (upstream #852): frozen capture stream
        # cancels the take with a clear error
        stall_s = float(self.cfg["recording"].get("stall_timeout_s", 8.0)
                        or 0.0)
        raw = getattr(self.recorder, "raw_path", None)
        if stall_s > 0 and raw is not None:
            self._tasks.spawn("stall", self._stall_monitor,
                              args=(Path(raw), stall_s))

    def _start_preview(self, raw_path) -> None:
        """Live transcription preview while recording (best-effort).

        Segmented engine first (constant per-tick decode cost, streaming
        preview on every backend, trailing-silence VAD auto-stop); the
        legacy whole-buffer engine stays as the faster-whisper fallback."""
        rcfg = self.cfg["recording"]
        if not rcfg.get("preview_enabled", True) or raw_path is None:
            return
        try:
            from .overlay import FluidOverlay
            from .preview import (
                NotifyPreview,
                PreviewEngine,
                SegmentedPreviewEngine,
                faster_whisper_transcriber,
                preview_transcriber,
            )
            mode = rcfg.get("preview_mode", "auto")
            if mode in ("auto", "overlay"):
                # FluidOverlay itself falls back to notifications when the
                # display/pill stack is unavailable.
                accent = "rewrite" if self._rewrite_mode \
                    else "command" if self._command_mode else "dictate"
                chips = {"copy_last": self._copy_last_transcript,
                         "paste_last": lambda: self.paste_last(),
                         "cancel": self.cancel}
                if not rcfg.get("overlay_chips", True):
                    chips = None
                display = FluidOverlay(
                    raw_path=Path(raw_path),
                    bottom_offset=int(rcfg.get("preview_bottom_offset", 64)),
                    size=rcfg.get("preview_overlay_size", "medium"),
                    mode=accent,
                    actions=chips)
                actual = "overlay" if display.using_overlay else "notify"
            else:
                display = NotifyPreview()
                actual = "notify"
            # captures the language at take start (a mid-take cycle press
            # re-resolves only the FINAL decode, in _process)
            language = self._language_detail()[0]
            engine = None
            kind = None
            if rcfg.get("preview_segmented", True):
                made = preview_transcriber(self.cfg, self.backend, language)
                if made is not None:
                    transcriber, bname = made
                    # track the last shown text so the send-countdown
                    # notice can be swapped out again on resume
                    shown = {"text": ""}

                    def _show(text: str, stable_chars=0,
                              _d=display, _s=shown) -> None:
                        _s["text"] = text
                        _s["stable"] = stable_chars
                        try:
                            _d.show(text, stable_chars)
                        except TypeError:  # single-arg display
                            _d.show(text)

                    engine = SegmentedPreviewEngine(
                        Path(raw_path), transcriber, _show,
                        interval=float(rcfg.get("preview_interval", 1.2)),
                        min_audio=float(rcfg.get("preview_min_audio", 1.0)),
                        segment_s=float(rcfg.get("preview_segment_s", 2.0)),
                        vad_silence_s=float(
                            rcfg.get("preview_vad_silence_s", 2.0)),
                        on_silence=self._vad_auto_stop,
                        send_phrase=(
                            str(rcfg.get("spoken_send_phrase", "send it"))
                            if rcfg.get("spoken_send_enabled") else ""),
                        send_countdown_s=float(
                            rcfg.get("spoken_send_countdown_s", 0.0) or 0.0),
                        on_send_countdown=self._on_send_countdown,
                        on_send_resume=self._on_send_resume)
                    kind = f"segmented/{bname}"
            if engine is None:
                model = getattr(self.backend, "_model", None)
                if self.backend is None or model is None:
                    display.close()
                    return  # not a ready faster-whisper backend
                engine = PreviewEngine(
                    Path(raw_path),
                    faster_whisper_transcriber(model, language),
                    display.show,
                    interval=float(rcfg.get("preview_interval", 1.2)),
                    min_audio=float(rcfg.get("preview_min_audio", 1.0)))
                kind = "legacy/faster-whisper"
            display.start()
            engine.start()
            self._preview = (engine, display)
            log(f"preview started ({actual}, {kind})")
        except Exception as e:
            log(f"WARN preview unavailable: {e}")

    def _stop_preview(self, finishing: bool = False) -> None:
        preview, self._preview = self._preview, None
        self._cancel_send_countdown()
        if preview is None:
            return
        engine, display = preview
        engine.stop()
        stats = getattr(engine, "stats", None)
        if stats and stats.get("decodes"):
            mean_ms = stats["decode_ms_sum"] / stats["decodes"]
            lag = max(0.0, stats["audio_s"] - stats["covered_s"])
            log(f"preview stats: decodes={stats['decodes']} "
                f"commits={stats['commits']} mean_decode_ms={mean_ms:.0f} "
                f"ticks={stats['ticks']} audio_s={stats['audio_s']:.1f} "
                f"lag_s={lag:.1f} tail_rewrites="
                f"{stats.get('tail_rewrites', 0)}"
                + (f" suppressed={stats['suppressed']}"
                   if stats.get("suppressed") else ""))
        if finishing:
            # Keep the pill up in its processing state (flat bars + shimmer,
            # like the Mac) until the final text is inserted.
            display.set_state("processing")
            self._closing_display = display
        else:
            display.close()

    def _vad_auto_stop(self) -> None:
        # Trailing-silence VAD (segmented preview thread): same finish path
        # as the max-duration watchdog, just a different reason. Re-check
        # under the lock - the user may have stopped the take just now.
        self._cancel_send_countdown()
        with self._lock:
            if not self.recording:
                return
            log("trailing silence detected, stopping")
            self._stop_recording_locked()

    # -- spoken-send quiet countdown (B7) -----------------------------------

    def _on_send_countdown(self) -> None:
        """Preview engine armed: phrase said + 0.5 s quiet. Show the notice
        (the engine pauses text emission while armed) and start the timer
        that finishes the take unless the user speaks again."""
        countdown = float(self.cfg["recording"].get(
            "spoken_send_countdown_s", 0.0) or 0.0)
        if countdown <= 0:
            return
        self._cancel_send_countdown()
        log("spoken-send quiet countdown armed "
            f"({countdown:.1f} s - speak to cancel)")
        display = self._preview[1] if self._preview else None
        if display is not None:
            try:
                display.show("⏎ sending… (speak to cancel)")
            except Exception:
                pass
        holder: dict[str, threading.Timer] = {}

        def _fire() -> None:
            self._send_countdown_stop(holder["t"])

        timer = self._tasks.prepare_timer("send-countdown", _fire,
                                          countdown)
        holder["t"] = timer
        self._send_countdown_timer = timer
        if timer is not None:
            timer.start()

    def _on_send_resume(self) -> None:
        """Speech resumed inside the countdown window: back to recording."""
        self._cancel_send_countdown()
        log("spoken-send countdown cancelled (speech resumed)")
        display = self._preview[1] if self._preview else None
        engine = self._preview[0] if self._preview else None
        last = getattr(engine, "last_text", "") if engine else ""
        if display is not None and last:
            try:
                display.show(last[-getattr(engine, "char_limit", 160):]
                             if len(last) > getattr(engine, "char_limit", 160)
                             else last)
            except Exception:
                pass

    def _cancel_send_countdown(self) -> None:
        timer, self._send_countdown_timer = \
            getattr(self, "_send_countdown_timer", None), None
        if timer is not None:
            timer.cancel()

    def _send_countdown_stop(self, timer: threading.Timer) -> None:
        # Identity check first: a stale timer from an earlier take must
        # never stop the current one.
        if timer is not getattr(self, "_send_countdown_timer", None):
            return
        self._cancel_send_countdown()
        with self._lock:
            if not self.recording:
                return
            log("spoken-send quiet countdown elapsed, stopping")
            self._stop_recording_locked()

    def _close_closing_display(self) -> None:
        display, self._closing_display = self._closing_display, None
        if display is not None:
            display.close()

    def _cancel_first_pcm_timer_locked(self) -> None:
        """Drop the first-PCM timer (caller holds self._lock): it must
        never outlive the take that created it."""
        timer, self._first_pcm_timer = self._first_pcm_timer, None
        if timer is not None:
            timer.cancel()

    def _check_first_pcm(self, recorder, wav: Path) -> None:
        # Identity check first, through the CAPTURED recorder object: a
        # timer that outlived its take (stop raced the firing, or a
        # settings change replaced self.recorder) must be a silent no-op
        # and never touch attributes of the CURRENT recorder. getattr
        # keeps this safe even for recorders without .path (test stubs).
        with self._lock:
            self._cancel_first_pcm_timer_locked()
            if not self.recording or self.recorder is not recorder:
                return
            take = getattr(recorder, "path", None)
            if take is None or Path(take) != wav:
                return
            # audio streams into the RAW file during recording (the WAV is
            # only written at stop); fall back to the wav for stub recorders
            probe = getattr(recorder, "raw_path", None) or wav
            try:
                got_pcm = probe is not None and probe.exists() \
                    and probe.stat().st_size > 2048
            except OSError:
                got_pcm = False
            if not got_pcm:
                if self._watchdog:
                    self._watchdog.cancel()
                    self._watchdog = None
                self.recorder.cancel()
                self.recording = False
                self._tray_recording(False)
                self._media.resume()
                msg = "microphone produced no audio (muted or wrong device?) - stopped"
                log(msg)
                ui.notify("SayItErmano", msg, enabled=self.cfg["notifications"]["enabled"])

    def _auto_stop(self) -> None:
        # Re-check under the lock: cancel()/shutdown() may have finished in
        # between the timer firing and now - never start anything new here.
        with self._lock:
            if not self.recording:
                return
            log("max duration reached, stopping")
            self._stop_recording_locked()

    def _stop_recording_locked(self) -> None:
        if self._watchdog:
            self._watchdog.cancel()
            self._watchdog = None
        self._cancel_first_pcm_timer_locked()
        self._stop_preview(finishing=True)
        # Stop cue fires at capture stop (upstream behavior), before waiting
        # for the recorder process to flush and exit.
        ui.play_sound("stop", self.cfg["sounds"]["volume"],
                      self.use_sounds and self.cfg["sounds"]["enabled"])
        wav = self.recorder.stop()
        self.recording = False
        self._touch_activity()
        self._tray_recording(False)
        self._media.resume()
        if wav is None or not Path(wav).exists() or Path(wav).stat().st_size < 200:
            log("no audio captured")
            self._rewrite_mode = False
            self._command_mode = False
            self._close_closing_display()
            if wav:
                Path(wav).unlink(missing_ok=True)
            return
        mode = "rewrite" if self._rewrite_mode else \
            "command" if self._command_mode else "dictate"
        context = self._rewrite_context
        self._rewrite_mode = False
        self._command_mode = False
        self._rewrite_context = None
        t = self._tasks.prepare(
            "process", self._process,
            args=(Path(wav), self._app_hint, mode, context))
        self._process_thread = t
        if t is not None:
            t.start()

    def cancel(self) -> None:
        with self._lock:
            if not self.recording:
                return
            self._cancel_locked()
        log("cancelled")
        ui.notify("SayItErmano", "Cancelled", enabled=self.cfg["notifications"]["enabled"])

    def _cancel_locked(self) -> None:
        """Cancel-the-take body (caller holds self._lock): no transcription,
        media resumed, mode flags cleared."""
        if self._watchdog:
            self._watchdog.cancel()
            self._watchdog = None
        self._cancel_first_pcm_timer_locked()
        self._stop_preview()
        self.recorder.cancel()
        self.recording = False
        self._tray_recording(False)
        self._media.resume()
        self._rewrite_mode = False
        self._command_mode = False
        self._profile_override = None

    _STALL_CHECK_S = 2.0

    def _stall_monitor(self, raw_path: Path, timeout_s: float) -> None:
        """Mid-take stream-stall watchdog (upstream #852): the capture file
        must keep growing while recording; frozen for timeout_s means the
        source died (PipeWire glitch, device vanished) - cancel the take
        with a clear error instead of recording air until max_seconds."""
        check = self._STALL_CHECK_S
        last, frozen = -1, 0.0
        while True:
            time.sleep(check)
            with self._lock:
                if not self.recording:
                    return
            try:
                size = raw_path.stat().st_size
            except OSError:
                size = last  # vanished mid-take: treat as frozen
            if size != last:
                last, frozen = size, 0.0
                continue
            frozen += check
            if frozen >= timeout_s:
                with self._lock:
                    if not self.recording:
                        return
                    log(f"audio stream stalled ({timeout_s:.0f}s without "
                        "new data) - cancelling")
                    self._cancel_locked()
                ui.notify("SayItErmano",
                          "Audio stream stalled — dictation cancelled",
                          enabled=self.cfg["notifications"]["enabled"])
                return

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
                result = self._ensure_backend().transcribe(
                    audio, self._language_detail()[0]) or {}
            finally:
                if converted_dir is not None:
                    _shutil.rmtree(converted_dir, ignore_errors=True)
            text = str(result.get("text") or "")
            if process:
                text = post_process(text, self.cfg)
            return {"ok": True, "path": str(path), "text": text,
                    "language": result.get("language"),
                    "duration_s": result.get("duration")}
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
            backend = self._ensure_backend()
            result = backend.transcribe(
                Path(wav), self._language_detail()[0]) or {}
            return {"ok": True, "duration_s": round(duration, 1),
                    "text": result.get("text", "")}
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
            self._profile_override = profile or None
            if self._profile_override:
                log(f"take starts with prompt profile '{self._profile_override}'")
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

    # -- language cycle (hotkey.language_key / general.language_cycle) ------

    def _cycle_list(self) -> list[str]:
        """The ordered cycle list, read LIVE on every use so Settings edits
        apply without a restart. Empty list = the feature is off even when
        the key is bound."""
        return list(((self.cfg.get("general", {}) or {})
                     .get("language_cycle")) or []) \
            if isinstance(self.cfg, dict) else []

    def _cycle_runtime(self) -> str | None:
        """The runtime cycle override for the next take: the cycle entry at
        the engaged index (clamped modulo a shrunk list), or None when the
        cycle is not engaged or the list was emptied underneath it."""
        cycle = self._cycle_list()
        if self._cycle_index is None or not cycle:
            return None
        return cycle[self._cycle_index % len(cycle)]

    def _language_detail(self) -> tuple[str, str]:
        """(effective language, source) for status/doctor/tooltip. The lazy
        backend may be None; language_detail resolves via the config key."""
        return backends.language_detail(self.cfg, self.backend,
                                        self._cycle_runtime() or "")

    def _cycle_language(self) -> dict:
        """Language-cycle hotkey handler: engage at index 0 on the first
        press, advance (with wrap-around) afterwards. The override is pure
        runtime state - never written to config. A mid-take press announces
        immediately; the final decode re-resolves at stop time (the preview
        engine keeps the language it captured at take start)."""
        if self._locked:
            return {"ok": False, "error": "locked"}  # session locked
        cycle = self._cycle_list()
        if not cycle:
            log("WARN language cycle: general.language_cycle is empty")
            ui.notify("SayItErmano",
                      "Language cycle is empty — set "
                      "general.language_cycle in Settings",
                      enabled=self.cfg["notifications"]["enabled"])
            return {"ok": False, "error": "empty cycle"}
        self._cycle_index = 0 if self._cycle_index is None \
            else (self._cycle_index + 1) % len(cycle)
        lang, source = self._language_detail()
        log(f"language cycle -> {lang} ({source})")
        self._announce_language(lang)
        self._refresh_tray()
        return {"ok": True, "language": lang, "source": source}

    def _announce_language(self, lang: str) -> None:
        """Eyes-free feedback on every cycle press: the live pill's badge
        while recording, the notify-fallback display's show(), or a fresh
        notify bubble. Never raises - an announcement must not break the
        cycle."""
        try:
            display = self._preview[1] if self._preview else None
            if display is not None:
                if callable(getattr(display, "set_badge", None)):
                    display.set_badge(f"lang: {lang}")
                    return
                if callable(getattr(display, "show", None)):
                    display.show(f"Language: {lang}")
                    return
            from .preview import NotifyPreview
            NotifyPreview().show(f"Language: {lang}")
        except Exception as e:  # noqa: BLE001 - best-effort feedback only
            log(f"WARN language announce failed: {e}")

    def _language_status(self) -> dict:
        """The additive \"language\" status block (CLI/GTK client/doctor)."""
        lang, source = self._language_detail()
        return {"effective": lang, "source": source,
                "cycle": self._cycle_list(),
                "cycle_engaged": self._cycle_runtime() is not None,
                "whitelist": list(((self.cfg.get("general", {}) or {})
                                   .get("language_whitelist")) or [])}

    # -- pipeline ------------------------------------------------------------

    def _ensure_backend(self):
        """Load the backend on demand (first take, take after an idle
        unload, onboarding tryout). The load lock serializes concurrent
        callers - the take-start background reload can race _process's
        synchronous load without double-loading."""
        with self._backend_load_lock:
            if self.backend is None:
                self.backend = self._backend_factory(self.cfg)
                log(f"speech backend: {self.backend.name}")
                self._idle_unloaded_at = None
                self._touch_activity()
        return self.backend

    def _process(self, wav: Path, app_hint: str | None,
                 mode: str = "dictate", rewrite_context: str | None = None) -> None:
        self.busy = True
        out: dict = {}
        try:
            try:
                backend = self._ensure_backend()
            except Exception as e:
                log(f"transcription failed: {e}")
                ui.notify("SayItErmano", f"Transcription failed: {e}",
                          enabled=self.cfg["notifications"]["enabled"])
                wav.unlink(missing_ok=True)
                return
            pipeline = self._pipeline_factory(self.cfg, backend)
            pipeline._profile_override = self._profile_override
            # sticky runtime cycle override (unlike the profile override,
            # deliberately NOT cleared in the finally below: cycle state
            # persists until cycled away or the daemon restarts)
            pipeline._language_override = self._cycle_runtime()
            out = pipeline.run(wav, app_hint, mode=mode,
                               rewrite_context=rewrite_context) or {}
            self.last_result = out
        finally:
            self.busy = False
            self._touch_activity()
            self._profile_override = None
            display, self._closing_display = self._closing_display, None
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
            self._begin_command(str(out.get("text", "")), app=app_hint)

    # -- command mode ---------------------------------------------------------

    def _begin_command(self, instruction: str,
                       app: str | None = None) -> None:
        """Turn 1: ask the model for the first proposal (background thread;
        the user is not blocked - not even by the LLM latency). `app` scopes
        the follow-up context store (last results in the SAME focused app)."""
        from . import command as command_mod
        if instruction.strip().lower() in command_mod.NEW_SESSION_PHRASES:
            if self._command_context is not None:
                self._command_context.clear(app)
            log("command context cleared (spoken 'new session')")
            ui.notify("SayItErmano", "Command context cleared",
                      enabled=self.cfg["notifications"]["enabled"])
            return
        self._command_entries = [{"kind": "user", "text": instruction}]
        self._command_panel(self._command_entries, status="Working...",
                            awaiting=None)

        def _work():
            factory = self._command_session_factory or command_mod.CommandSession
            if self._command_context is None:
                self._command_context = command_mod.CommandContextStore()
            session = factory(self.cfg, context_store=self._command_context,
                              app=app)
            try:
                proposal = session.start(instruction)
            except command_mod.CommandError as e:
                with self._lock:
                    self.busy = False
                log(f"command mode failed: {e}")
                ui.notify("SayItErmano", f"Command mode failed: {e}",
                          enabled=self.cfg["notifications"]["enabled"])
                return
            if proposal is None:
                with self._lock:
                    self.busy = False
                ui.notify("SayItErmano",
                          session.summary or "Command mode: nothing to run.",
                          enabled=self.cfg["notifications"]["enabled"])
                return
            with self._lock:                 # atomic handoff to the pending state
                self._command_session = session
                self._command_pending = True
                self.busy = False            # waiting for the user, not busy
            self._present_proposal(session, proposal)

        def _guarded():
            try:
                _work()
            except Exception as e:  # noqa: BLE001 - never strand `busy`
                log(f"command mode failed: {e}")
                ui.notify("SayItErmano", f"Command mode failed: {e}",
                          enabled=self.cfg["notifications"]["enabled"])
                self._end_command_session()

        with self._lock:
            if self.busy or self._command_pending:
                return
            self.busy = True
        self._tasks.spawn("command", _guarded)

    def _rerun_command(self, command: str,
                       purpose: str | None = None) -> dict:
        """History Commands view 'Re-run' (v2): re-post the exact stored
        command as a PENDING proposal - the user confirms with the hotkey
        exactly like a fresh voice proposal (strong confirm included when
        destructive). NOTHING executes here: this only ever creates a
        pending proposal; CommandSession.confirm() stays the single
        execution site. No LLM call is needed to propose."""
        from . import command as command_mod
        with self._lock:
            if self.recording or self.busy or self._command_pending:
                return {"ok": False, "error": "daemon busy"}
        ready = command_mod.command_mode_ready(self.cfg)
        if ready:
            return {"ok": False, "error": ready}
        if self._command_context is None:
            self._command_context = command_mod.CommandContextStore()
        factory = self._command_session_factory or command_mod.CommandSession
        session = factory(self.cfg, context_store=self._command_context)
        try:
            proposal = session.preset(command, purpose)
        except command_mod.CommandError as e:
            return {"ok": False, "error": str(e)}
        self._command_entries = [{"kind": "user",
                                  "text": session.instruction or ""}]
        with self._lock:                 # atomic handoff to the pending state
            self._command_session = session
            self._command_pending = True
            self.busy = False            # waiting for the user, not busy
        self._present_proposal(session, proposal)
        return {"ok": True, "pending": True, "command": proposal.command}

    def _command_panel(self, entries: list[dict], status: str | None,
                       awaiting: str | None):
        """Live conversation panel (best-effort). Reuses the running panel
        when present; falls back to None headlessly."""
        try:
            from .overlay import CommandPanel
            panel = self._command_display
            if panel is not None and panel.using_overlay:
                panel.update(entries, status=status, awaiting=awaiting)
                return panel
            rcfg = self.cfg["recording"]
            panel = CommandPanel(
                bottom_offset=int(rcfg.get("preview_bottom_offset", 64)))
            if not panel.using_overlay:
                panel.close()
                return None
            panel.update(entries, status=status, awaiting=awaiting)
            panel.start()
            self._command_display = panel
            return panel
        except Exception as e:  # noqa: BLE001 - never block the agent loop
            log(f"WARN command panel unavailable: {e}")
            return None

    def _present_proposal(self, session, proposal) -> None:
        """Awaiting-confirmation UX: conversation panel, armed Escape grab,
        notification, confirm watchdog. Call with no lock held. A
        destructive proposal arms the STRONG confirmation (two presses,
        amber pill warning) - the first press only arms."""
        self._command_destructive_armed = False
        try:
            entry = {"kind": "proposal", "text": proposal.command,
                     "sub": proposal.purpose}
            awaiting = "run: command key · Esc"
            if proposal.destructive:
                entry["destructive"] = True
                awaiting = "⚠ destructive — press command key AGAIN to run · Esc"
            self._command_entries = (self._command_entries + [entry])[-8:]
            self._command_panel(self._command_entries, status=None,
                                awaiting=awaiting)
        except Exception as e:  # noqa: BLE001 - never block confirmation
            log(f"WARN command pill unavailable: {e}")
        if self._command_hotkey:
            try:
                self._command_hotkey.set_recording(True)  # arm Escape grab
            except Exception:
                pass
        purpose = proposal.purpose or ""
        body = (f"{purpose}\n" if purpose else "") \
            + f"$ {proposal.command}\n" \
            + "Press the command hotkey to run · Esc to cancel"
        if proposal.destructive:
            body = "⚠ DESTRUCTIVE\n" + body
        ui.notify("SayItErmano — run this command?", body,
                  enabled=self.cfg["notifications"]["enabled"])
        timer = self._tasks.prepare_timer(
            "command-confirm", self._on_confirm_timeout,
            float(self.cfg["command"].get("confirm_timeout_s", 120.0)))
        self._command_timer = timer
        if timer is not None:
            timer.start()

    def _confirm_pending_command(self) -> None:
        """Hotkey-confirmed: execute (the only path into
        CommandSession.confirm), then either present the next proposal or
        finish. Destructive proposals need the STRONG confirmation: the
        first press only arms (fresh hint + restarted watchdog); the second
        takes this normal path. Non-destructive: single press, as always."""
        arm = False
        with self._lock:
            if not self._command_pending or self.busy or self.recording:
                return
            session = self._command_session
            proposal = session.pending if session is not None else None
            if proposal is not None and proposal.destructive \
                    and not self._command_destructive_armed:
                self._command_destructive_armed = True
                arm = True
            else:
                self._command_destructive_armed = False
                self._command_pending = False
                self.busy = True                 # atomic with the flag clear
        if arm:
            self._arm_destructive_confirm(proposal)
            return
        session = self._command_session      # never None while pending
        # the conversation panel survives the pending-UX teardown
        panel, self._command_display = self._command_display, None
        self._teardown_pending_ux()
        self._command_display = panel

        def _work():
            from . import command as command_mod
            try:
                proposal = session.confirm()
            except command_mod.CommandError as e:
                log(f"command mode failed: {e}")
                ui.notify("SayItErmano", f"Command mode failed: {e}",
                          enabled=self.cfg["notifications"]["enabled"])
                self._end_command_session()
                return
            outcome = session.executed[-1] if session.executed else None
            if outcome is not None:          # result via notification + history
                brief = (outcome.output or outcome.error or "").strip()[:200]
                ui.notify("SayItErmano",
                          f"$ {outcome.command} → exit {outcome.exit_code}"
                          + (f"\n{brief}" if brief else ""),
                          enabled=self.cfg["notifications"]["enabled"])
                self._command_entries = (self._command_entries + [
                    {"kind": "ok" if outcome.success else "fail",
                     "text": f"$ {outcome.command} · "
                             f"exit {outcome.exit_code}"}])[-8:]
            if proposal is None:
                self._command_entries = (self._command_entries + [
                    {"kind": "summary",
                     "text": session.summary or "Command finished."}])[-8:]
                self._command_panel(self._command_entries, status=None,
                                    awaiting=None)
                ui.notify("SayItErmano",
                          (session.summary or "Command finished.")
                          + (" (step limit reached)" if session.exhausted
                             else ""),
                          enabled=self.cfg["notifications"]["enabled"])
                self._tasks.schedule("command-panel-close",
                                     self._close_command_panel, 8.0)
                self._end_command_session(close_panel=False)
                return
            self._command_panel(self._command_entries, status="Working...",
                                awaiting=None)
            with self._lock:
                self._command_pending = True
                self.busy = False
            self._present_proposal(session, proposal)

        def _guarded():
            try:
                _work()
            except Exception as e:  # noqa: BLE001 - never strand `busy`
                log(f"command mode failed: {e}")
                ui.notify("SayItErmano", f"Command mode failed: {e}",
                          enabled=self.cfg["notifications"]["enabled"])
                self._end_command_session()

        self._tasks.spawn("command", _guarded)

    def _arm_destructive_confirm(self, proposal) -> None:
        """First press on a destructive proposal: NOTHING executes. Refresh
        the pill to the again-to-CONFIRM hint, re-notify and restart the
        confirm watchdog (the old timer is cancelled - never stacked)."""
        if self._command_timer:
            self._command_timer.cancel()
            self._command_timer = None
        self._command_panel(
            self._command_entries, status=None,
            awaiting="⚠ press command key AGAIN to CONFIRM · Esc cancels")
        purpose = proposal.purpose or ""
        body = (f"{purpose}\n" if purpose else "") \
            + f"$ {proposal.command}\n" \
            + "⚠ destructive: press the command hotkey AGAIN to CONFIRM " \
              "· Esc to cancel"
        ui.notify("SayItErmano — ⚠ destructive", body,
                  enabled=self.cfg["notifications"]["enabled"])
        timer = self._tasks.prepare_timer(
            "command-confirm", self._on_confirm_timeout,
            float(self.cfg["command"].get("confirm_timeout_s", 120.0)))
        self._command_timer = timer
        if timer is not None:
            timer.start()

    def cancel_pending_command(self) -> None:
        """Escape on a pending proposal (or a test): nothing executes."""
        with self._lock:
            if not self._command_pending:
                return
            self._command_pending = False
        session, self._command_session = self._command_session, None
        self._teardown_pending_ux()
        if session is not None:
            session.cancel()
        ui.notify("SayItErmano", "Command cancelled",
                  enabled=self.cfg["notifications"]["enabled"])

    def _on_confirm_timeout(self) -> None:
        if self._command_pending:
            self.cancel_pending_command()
            ui.notify("SayItErmano", "Command mode: confirmation timed out",
                      enabled=self.cfg["notifications"]["enabled"])

    def _teardown_pending_ux(self) -> None:
        self._command_destructive_armed = False
        if self._command_timer:
            self._command_timer.cancel()
            self._command_timer = None
        if self._command_hotkey:
            try:
                self._command_hotkey.set_recording(False)
            except Exception:
                pass
        display, self._command_display = self._command_display, None
        if display is not None:
            try:
                display.close()
            except Exception:
                pass

    def _close_command_panel(self) -> None:
        if self._command_session is not None:
            return  # a new command session reused the panel - leave it up
        panel, self._command_display = self._command_display, None
        if panel is not None:
            try:
                panel.close()
            except Exception:
                pass

    def _end_command_session(self, close_panel: bool = True) -> None:
        with self._lock:
            self._command_pending = False
            self._command_destructive_armed = False
            self.busy = False
        self._command_session = None
        if close_panel:
            self._teardown_pending_ux()
