"""CaptureCoordinator: the daemon's take lifecycle owner (P1.2).

Everything between "the user pressed the hotkey" and "here is a finished
wav for the pipeline" - recording state, the live preview (pill display +
preview engines), trailing-silence VAD, the first-PCM/stall/max-duration
watchdogs, media pause, and the stop/cancel paths - extracted from
``Daemon`` (fluidvoice/daemon.py) along the plan's decomposition:

* start/stop/cancel  ``start_locked``/``stop_locked``/``cancel_locked``
                (caller holds the shared daemon lock, exactly the
                pre-split discipline) and the public ``cancel()``
* timers         the max-duration ``watchdog``, the ``first-pcm``
                liveness timer (tracked and cancelled by every take-end
                path; its callback re-validates recorder identity) and
                the mid-take ``stall`` stream monitor - all through
                RuntimeTasks
* preview        ``start_preview``/``stop_preview`` (segmented engine
                first, legacy whole-buffer fallback), the spoken-send
                quiet countdown, and the closing display the pipeline
                finishes (the done beat)
* media pause    the MediaController lives here (pause at take start,
                resume at every take end)
* take metadata  the per-take mode (dictate/rewrite/command), rewrite
                context, app hint and profile override ride along

The pipeline itself stays in fluidvoice/pipeline.py: a finished take is
handed to the daemon through the ``on_take_complete`` callback, which
spawns the processing thread. Explicit dependencies, no singletons, no
daemon import: the cfg dict, the shared state lock, RuntimeTasks, a
recorder provider (the daemon may rebuild the recorder under it), and
callbacks OUT.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import insertion
from .media import MediaController
from .recorder import RecorderError
from .runtime_tasks import RuntimeTasks

__all__ = ["CaptureCoordinator"]

Logger = Callable[[str], None]
Notifier = Callable[[str, str], None]
SoundPlayer = Callable[[str], None]


class CaptureCoordinator:
    """Owns the recording state machine and everything a take touches."""

    STALL_CHECK_S = 2.0

    def __init__(self, cfg: dict, tasks: RuntimeTasks,
                 lock: threading.Lock, *,
                 recorder_provider: Callable[[], Any],
                 log: Logger,
                 notify: Notifier,
                 play_sound: SoundPlayer,
                 on_recording_change: Callable[[bool], None],
                 on_take_start: Callable[[], None],
                 on_take_complete: Callable[[Path, str | None, str, str | None], None],
                 on_activity: Callable[[], None],
                 language_provider: Callable[[], str],
                 backend_provider: Callable[[], Any],
                 on_copy_last: Callable[[], None],
                 on_paste_last: Callable[[], Any]):
        self.cfg = cfg
        self._tasks = tasks
        self._lock = lock
        self._recorder = recorder_provider
        self._log = log
        self._notify = notify
        self._play_sound = play_sound
        self._on_recording_change = on_recording_change
        self._on_take_start = on_take_start
        self._on_take_complete = on_take_complete
        self._on_activity = on_activity
        self._language = language_provider
        self._backend = backend_provider
        self._on_copy_last = on_copy_last
        self._on_paste_last = on_paste_last
        self._media = MediaController(log=log)
        # -- take state (transitions under the shared lock) ----------------
        self.recording = False
        self.take_mode = "dictate"        # dictate | rewrite | command
        self.rewrite_context: str | None = None
        self.app_hint: str | None = None  # focused window at take start
        self.profile_override: str | None = None  # profiled shortcut take
        # -- supervised timers (handles stay raw Timer objects) -------------
        self.watchdog: threading.Timer | None = None       # max duration
        self.first_pcm_timer: threading.Timer | None = None
        self.send_countdown_timer: threading.Timer | None = None
        # -- preview (engine, display) + the pill awaiting the done beat ----
        self.preview: Any = None
        self.closing_display: Any = None

    # -- take lifecycle (callers hold the shared lock) ------------------------

    def start_locked(self, mode: str = "dictate",
                     rewrite_context: str | None = None) -> None:
        self.take_mode = mode
        self.rewrite_context = rewrite_context
        self.app_hint = insertion.active_window_class()
        fd, tmp = tempfile.mkstemp(prefix="sayitermano-", suffix=".wav")
        os.close(fd)
        recorder = self._recorder()
        try:
            recorder.start(Path(tmp))
        except RecorderError as e:
            self._log(f"recorder error: {e}")
            self._notify("SayItErmano", f"Recording failed: {e}")
            Path(tmp).unlink(missing_ok=True)
            return
        self.recording = True
        self._on_activity()
        self._on_take_start()
        self._on_recording_change(True)
        if self.cfg["recording"].get("pause_media", True):
            self._media.pause_if_playing()  # upstream: only what's playing
        self._play_sound("start")
        self._log(f"recording (app={self.app_hint or '?'})")
        max_s = float(self.cfg["recording"].get("max_seconds", 300))
        t = self._tasks.prepare_timer("watchdog", self.auto_stop, max_s)
        self.watchdog = t
        if t is not None:
            t.start()
        # Upstream firstPCMTimeout (2s): a live-but-silent source (muted mic,
        # wrong device, Bluetooth glitch) should fail fast, not record air
        # until max_seconds.
        self.start_preview(getattr(recorder, "raw_path", None))
        pcm_timeout = float(self.cfg["recording"].get("first_pcm_timeout", 2.0))
        if pcm_timeout > 0:
            # tracked + cancelled by every take-end path (stop/cancel/
            # shutdown): the callback re-validates identity anyway, but a
            # cancelled timer is the guarantee it never even fires stale
            self.cancel_first_pcm_timer_locked()
            t = self._tasks.prepare_timer(
                "first-pcm", self.check_first_pcm, pcm_timeout,
                args=(recorder, Path(tmp)))
            self.first_pcm_timer = t
            if t is not None:
                t.start()
        # mid-take stall watchdog (upstream #852): frozen capture stream
        # cancels the take with a clear error
        stall_s = float(self.cfg["recording"].get("stall_timeout_s", 8.0)
                        or 0.0)
        raw = getattr(recorder, "raw_path", None)
        if stall_s > 0 and raw is not None:
            self._tasks.spawn("stall", self.stall_monitor,
                              args=(Path(raw), stall_s))

    def stop_locked(self) -> None:
        if self.watchdog:
            self.watchdog.cancel()
            self.watchdog = None
        self.cancel_first_pcm_timer_locked()
        self.stop_preview(finishing=True)
        # Stop cue fires at capture stop (upstream behavior), before waiting
        # for the recorder process to flush and exit.
        self._play_sound("stop")
        wav = self._recorder().stop()
        self.recording = False
        self._on_activity()
        self._on_recording_change(False)
        self._media.resume()
        if wav is None or not Path(wav).exists() \
                or Path(wav).stat().st_size < 200:
            self._log("no audio captured")
            self.take_mode = "dictate"
            self.rewrite_context = None
            self.close_closing_display()
            if wav:
                Path(wav).unlink(missing_ok=True)
            return
        mode = self.take_mode
        context = self.rewrite_context
        self.take_mode = "dictate"
        self.rewrite_context = None
        self._on_take_complete(Path(wav), self.app_hint, mode, context)

    def cancel_locked(self) -> None:
        """Cancel-the-take body (caller holds the lock): no transcription,
        media resumed, mode flags cleared."""
        if self.watchdog:
            self.watchdog.cancel()
            self.watchdog = None
        self.cancel_first_pcm_timer_locked()
        self.stop_preview()
        self._recorder().cancel()
        self.recording = False
        self._on_recording_change(False)
        self._media.resume()
        self.take_mode = "dictate"
        self.rewrite_context = None
        self.profile_override = None

    def cancel(self) -> None:
        """Public cancel path (Escape / socket): discard the take."""
        with self._lock:
            if not self.recording:
                return
            self.cancel_locked()
        self._log("cancelled")
        self._notify("SayItErmano", "Cancelled")

    def abort_locked(self) -> None:
        """Shutdown's take-state teardown (caller holds the lock): cancel
        the recorder and both take timers. Serialized under the lock
        because these fields race the hotkey thread's toggle (N1)."""
        if self.recording:
            self._recorder().cancel()
            self.recording = False
        if self.watchdog:
            self.watchdog.cancel()
        if self.first_pcm_timer:
            self.first_pcm_timer.cancel()
            self.first_pcm_timer = None

    # -- max-duration watchdog --------------------------------------------------

    def auto_stop(self) -> None:
        # Re-check under the lock: cancel()/shutdown() may have finished in
        # between the timer firing and now - never start anything new here.
        with self._lock:
            if not self.recording:
                return
            self._log("max duration reached, stopping")
            self.stop_locked()

    # -- first-PCM liveness timer -----------------------------------------------

    def cancel_first_pcm_timer_locked(self) -> None:
        """Drop the first-PCM timer (caller holds the lock): it must
        never outlive the take that created it."""
        timer, self.first_pcm_timer = self.first_pcm_timer, None
        if timer is not None:
            timer.cancel()

    def check_first_pcm(self, recorder, wav: Path) -> None:
        # Identity check first, through the CAPTURED recorder object: a
        # timer that outlived its take (stop raced the firing, or a
        # settings change replaced the recorder) must be a silent no-op
        # and never touch attributes of the CURRENT recorder. getattr
        # keeps this safe even for recorders without .path (test stubs).
        with self._lock:
            self.cancel_first_pcm_timer_locked()
            if not self.recording or self._recorder() is not recorder:
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
                if self.watchdog:
                    self.watchdog.cancel()
                    self.watchdog = None
                recorder.cancel()
                self.recording = False
                self._on_recording_change(False)
                self._media.resume()
                msg = ("microphone produced no audio "
                       "(muted or wrong device?) - stopped")
                self._log(msg)
                self._notify("SayItErmano", msg)

    # -- mid-take stream-stall watchdog (upstream #852) -------------------------

    def stall_monitor(self, raw_path: Path, timeout_s: float) -> None:
        """The capture file must keep growing while recording; frozen for
        timeout_s means the source died (PipeWire glitch, device vanished)
        - cancel the take with a clear error instead of recording air
        until max_seconds."""
        check = self.STALL_CHECK_S
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
                    self._log(f"audio stream stalled ({timeout_s:.0f}s "
                              "without new data) - cancelling")
                    self.cancel_locked()
                self._notify("SayItErmano",
                             "Audio stream stalled — dictation cancelled")
                return

    # -- live preview -----------------------------------------------------------

    def start_preview(self, raw_path) -> None:
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
                accent = ("rewrite" if self.take_mode == "rewrite"
                          else "command" if self.take_mode == "command"
                          else "dictate")
                chips = {"copy_last": self._on_copy_last,
                         "paste_last": self._on_paste_last,
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
            # re-resolves only the FINAL decode, in the pipeline)
            language = self._language()
            engine = None
            kind = None
            backend = self._backend()
            if rcfg.get("preview_segmented", True):
                made = preview_transcriber(self.cfg, backend, language)
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
                        conf_gate=bool(rcfg.get("preview_conf_gate", True)),
                        vad_silence_s=float(
                            rcfg.get("preview_vad_silence_s", 2.0)),
                        on_silence=self.vad_auto_stop,
                        send_phrase=(
                            str(rcfg.get("spoken_send_phrase", "send it"))
                            if rcfg.get("spoken_send_enabled") else ""),
                        send_countdown_s=float(
                            rcfg.get("spoken_send_countdown_s", 0.0) or 0.0),
                        on_send_countdown=self.on_send_countdown,
                        on_send_resume=self.on_send_resume)
                    kind = f"segmented/{bname}"
            if engine is None:
                model = getattr(backend, "_model", None)
                if backend is None or model is None:
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
            self.preview = (engine, display)
            self._log(f"preview started ({actual}, {kind})")
        except Exception as e:  # noqa: BLE001 - preview is best-effort
            self._log(f"WARN preview unavailable: {e}")

    # -- preview teardown + VAD auto-stop ---------------------------------------

    def stop_preview(self, finishing: bool = False) -> None:
        preview, self.preview = self.preview, None
        self.cancel_send_countdown()
        if preview is None:
            return
        engine, display = preview
        engine.stop()
        stats = getattr(engine, "stats", None)
        if stats and stats.get("decodes"):
            mean_ms = stats["decode_ms_sum"] / stats["decodes"]
            lag = max(0.0, stats["audio_s"] - stats["covered_s"])
            self._log(f"preview stats: decodes={stats['decodes']} "
                      f"commits={stats['commits']} "
                      f"mean_decode_ms={mean_ms:.0f} "
                      f"ticks={stats['ticks']} audio_s={stats['audio_s']:.1f} "
                      f"lag_s={lag:.1f} tail_rewrites="
                      f"{stats.get('tail_rewrites', 0)}"
                      + (f" suppressed={stats['suppressed']}"
                         if stats.get("suppressed") else "")
                      + (f" lowconf={stats['lowconf']}"
                         if stats.get("lowconf") else "")
                      + (" silenced" if stats.get("silenced") else ""))
            if stats.get("silenced"):
                # close the loop on the mid-take ellipsis: tell the user
                # WHY the preview went quiet - and that the final text
                # (full-take decode) is unaffected
                self._notify(
                    "SayItErmano",
                    "Live preview silenced — audio quality too low for "
                    "live text; the final transcription still uses the "
                    "full take")
        if finishing:
            # Keep the pill up in its processing state (flat bars + shimmer,
            # like the Mac) until the final text is inserted.
            display.set_state("processing")
            self.closing_display = display
        else:
            display.close()

    def vad_auto_stop(self) -> None:
        # Trailing-silence VAD (segmented preview thread): same finish path
        # as the max-duration watchdog, just a different reason. Re-check
        # under the lock - the user may have stopped the take just now.
        self.cancel_send_countdown()
        with self._lock:
            if not self.recording:
                return
            self._log("trailing silence detected, stopping")
            self.stop_locked()

    # -- spoken-send quiet countdown (B7) ---------------------------------------

    def on_send_countdown(self) -> None:
        """Preview engine armed: phrase said + 0.5 s quiet. Show the notice
        (the engine pauses text emission while armed) and start the timer
        that finishes the take unless the user speaks again."""
        countdown = float(self.cfg["recording"].get(
            "spoken_send_countdown_s", 0.0) or 0.0)
        if countdown <= 0:
            return
        self.cancel_send_countdown()
        self._log("spoken-send quiet countdown armed "
                  f"({countdown:.1f} s - speak to cancel)")
        display = self.preview[1] if self.preview else None
        if display is not None:
            try:
                display.show("⏎ sending… (speak to cancel)")
            except Exception:  # noqa: BLE001 - display is best-effort
                pass
        holder: dict[str, threading.Timer] = {}

        def _fire() -> None:
            self.send_countdown_stop(holder["t"])

        timer = self._tasks.prepare_timer("send-countdown", _fire,
                                          countdown)
        holder["t"] = timer
        self.send_countdown_timer = timer
        if timer is not None:
            timer.start()

    def on_send_resume(self) -> None:
        """Speech resumed inside the countdown window: back to recording."""
        self.cancel_send_countdown()
        self._log("spoken-send countdown cancelled (speech resumed)")
        display = self.preview[1] if self.preview else None
        engine = self.preview[0] if self.preview else None
        last = getattr(engine, "last_text", "") if engine else ""
        if display is not None and last:
            try:
                display.show(last[-getattr(engine, "char_limit", 160):]
                             if len(last) > getattr(engine, "char_limit", 160)
                             else last)
            except Exception:  # noqa: BLE001 - display is best-effort
                pass

    def cancel_send_countdown(self) -> None:
        timer, self.send_countdown_timer = self.send_countdown_timer, None
        if timer is not None:
            timer.cancel()

    def send_countdown_stop(self, timer: threading.Timer) -> None:
        # Identity check first: a stale timer from an earlier take must
        # never stop the current one.
        if timer is not self.send_countdown_timer:
            return
        self.cancel_send_countdown()
        with self._lock:
            if not self.recording:
                return
            self._log("spoken-send quiet countdown elapsed, stopping")
            self.stop_locked()

    # -- the closing display (the pipeline finishes it: done beat) --------------

    def close_closing_display(self) -> None:
        display, self.closing_display = self.closing_display, None
        if display is not None:
            display.close()

    def take_closing_display(self) -> Any:
        """Hand the closing display to the processing thread and clear it."""
        display, self.closing_display = self.closing_display, None
        return display

    # -- language announcement (eyes-free cycle feedback) -----------------------

    def announce_language(self, lang: str) -> None:
        """The live pill's badge while recording, the notify-fallback
        display's show(), or a fresh notify bubble. Never raises - an
        announcement must not break the cycle."""
        try:
            display = self.preview[1] if self.preview else None
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
            self._log(f"WARN language announce failed: {e}")
