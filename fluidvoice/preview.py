"""Live streaming preview: rolling transcription while recording.

The recorder captures headerless raw PCM; PreviewEngine periodically wraps
the accumulated bytes in a WAV (in memory) and transcribes the growing
prefix through the faster-whisper model. Partial text goes to a pluggable
display: the Mac-style pill overlay (overlay.FluidOverlay) or a replaceable
desktop notification (works everywhere).
"""
from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Callable

from .audio_utils import raw_to_wav_bytes


class PreviewEngine:
    """Background thread that turns a growing raw PCM file into partial text."""

    def __init__(self, raw_path: Path, transcriber: Callable[[bytes], str],
                 on_text: Callable[[str], None], *,
                 sample_rate: int = 16000, interval: float = 1.2,
                 min_audio: float = 1.0, char_limit: int = 160):
        self.raw_path = raw_path
        self.transcriber = transcriber
        self.on_text = on_text
        self.sample_rate = sample_rate
        self.interval = interval
        self.min_bytes = int(min_audio * sample_rate * 2)
        self.char_limit = char_limit
        self._stop = threading.Event()
        self._busy = False
        self._thread: threading.Thread | None = None
        self.last_text = ""

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="fluidvoice-preview",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            if self._busy:  # previous pass still transcribing - skip this tick
                continue
            try:
                raw = self._read_new()
            except OSError:
                continue
            if raw is None or len(raw) < self.min_bytes:
                continue
            self._busy = True
            try:
                wav = raw_to_wav_bytes(raw, self.sample_rate)
                text = self.transcriber(wav).strip()
                if text and text != self.last_text:
                    self.last_text = text
                    self._emit(text)
            except Exception:
                pass  # preview is best-effort; the final pass is authoritative
            finally:
                self._busy = False

    def _read_new(self) -> bytes | None:
        try:
            with open(self.raw_path, "rb") as fh:
                return fh.read()
        except FileNotFoundError:
            return None

    def _emit(self, text: str) -> None:
        shown = text if len(text) <= self.char_limit else "…" + text[-self.char_limit:]
        try:
            self.on_text(shown)
        except Exception:
            pass


def faster_whisper_transcriber(model, language: str | None) -> Callable[[bytes], str]:
    """Build a bytes->text transcriber from a loaded faster-whisper model."""
    lang = None if language in (None, "", "auto") else language

    def transcribe(wav_bytes: bytes) -> str:
        segments, _ = model.transcribe(io.BytesIO(wav_bytes), language=lang,
                                       beam_size=1, condition_on_previous_text=False,
                                       without_timestamps=True)
        return " ".join(s.text.strip() for s in segments if s.text.strip())
    return transcribe


# ---------------------------------------------------------------------------
# Segmented streaming preview (requests/streaming-finalization.md, phase 1)
# ---------------------------------------------------------------------------

# Trailing-silence VAD frame gate. The RMS threshold is ported from the
# short-recording silence gate (audio_utils.is_silent's max_frame_rms, 20 ms
# frames); the ZCR guard keeps unvoiced fricatives ("sss", "fff" — high
# zero-crossing rate at low energy) counted as speech.
VAD_FRAME_RMS = 0.0045
VAD_ZCR_MAX = 0.45
VAD_FRAME_S = 0.02


def trailing_silence_s(raw: bytes, sample_rate: int = 16000) -> float:
    """Seconds of consecutive silent 20 ms frames at the end of raw PCM."""
    import array
    width = 2  # s16 mono
    frame_bytes = int(sample_rate * VAD_FRAME_S) * width
    if not raw or frame_bytes < 4:
        return 0.0
    samples = array.array("h")
    usable = raw[: (len(raw) // width) * width]
    samples.frombytes(usable)
    silent_frames = 0
    n = len(samples)
    step = int(sample_rate * VAD_FRAME_S)
    pos = n
    while pos - step >= 0:
        frame = samples[pos - step: pos]
        pos -= step
        rms = (sum(s * s for s in frame) / len(frame)) ** 0.5 / 32768.0
        if rms >= VAD_FRAME_RMS:
            break
        sign_changes = sum(
            1 for a, b in zip(frame, frame[1:]) if (a < 0) != (b < 0))
        if sign_changes / max(1, len(frame) - 1) >= VAD_ZCR_MAX:
            break  # low-energy fricative, still speech
        silent_frames += 1
    return silent_frames * VAD_FRAME_S


def join_tail(committed: str, tail: str) -> str:
    """Return the tail portion of the preview text, dropping words the
    decoder re-emitted from the overlap region (windows share 50% of their
    audio). Empty tail means nothing new to show."""
    if not tail:
        return ""
    if not committed:
        return tail
    cwords, twords = committed.split(), tail.split()
    for n in range(min(len(cwords), len(twords), 12), 0, -1):
        if cwords[-n:] == twords[:n]:
            return " ".join(twords[n:])
    return tail


class SegmentedPreviewEngine:
    """Streaming preview with constant per-tick decode cost.

    The take is cut into fixed windows of segment_s seconds at a 50% hop.
    Every even window (0, 2, 4, ...) tiles the stream with no overlap and is
    decoded exactly once, in order, and COMMITTED: committed text is stable
    and monotone, never re-decoded. Each tick decodes at most one window —
    either the oldest due commit, or the live tail (the newest segment_s
    slice, partial allowed) for fresh words. The last committed segment rides
    along as initial_prompt where the backend supports it. A trailing-silence
    VAD can auto-stop the take (energy + zero-crossing, no ML).
    """

    def __init__(self, raw_path: Path, transcriber: Callable[[bytes, str | None], str],
                 on_text: Callable[[str], None], *,
                 sample_rate: int = 16000, interval: float = 1.2,
                 min_audio: float = 1.0, char_limit: int = 160,
                 segment_s: float = 2.0, vad_silence_s: float = 2.0,
                 on_silence: Callable[[], None] | None = None,
                 send_phrase: str = "",
                 send_countdown_s: float = 0.0,
                 on_send_countdown: Callable[[], None] | None = None,
                 on_send_resume: Callable[[], None] | None = None):
        self.raw_path = raw_path
        self.transcriber = transcriber
        self.on_text = on_text
        self.sample_rate = sample_rate
        self.interval = interval
        self.min_bytes = int(min_audio * sample_rate * 2)
        self.char_limit = char_limit
        self.segment_s = segment_s
        self.vad_silence_s = vad_silence_s
        self.on_silence = on_silence
        # spoken-send quiet countdown (B7): 0.5 s of trailing quiet while
        # the rolling text ends with the phrase arms the daemon-side
        # countdown; resumed speech cancels it (see daemon._on_send_*)
        self.send_phrase = (send_phrase or "").strip()
        self.send_countdown_s = float(send_countdown_s or 0.0)
        self.on_send_countdown = on_send_countdown
        self.on_send_resume = on_send_resume
        self._send_armed = False
        # audio position where the rolling text last ENDED with the phrase;
        # the tail decode degrades once speech stops (its window slides
        # into silence and drops the final words), so the arm must accept
        # a recently-seen phrase instead of a currently-visible one
        self._phrase_seen_s: float | None = None
        self.hop_s = segment_s / 2.0
        self._stop = threading.Event()
        self._busy = False
        self._thread: threading.Thread | None = None
        self.last_text = ""
        self.committed: list[str] = []
        self._next_commit = 0          # even window index never decoded yet
        self._silence_fired = False
        self.stats = {"decodes": 0, "commits": 0, "decode_ms_sum": 0.0,
                      "ticks": 0, "audio_s": 0.0, "covered_s": 0.0}

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(
            target=self._loop, name="fluidvoice-preview-seg", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        # The VAD on_silence callback stops the daemon's recording from
        # THIS thread; joining ourselves there would deadlock - the loop
        # exits on its own at the next wait().
        if (self._thread and self._thread is not threading.current_thread()
                and self._thread.is_alive()):
            self._thread.join(timeout=timeout)
        self._thread = None

    # -- helpers -----------------------------------------------------------

    def _audio_bytes(self) -> bytes | None:
        try:
            with open(self.raw_path, "rb") as fh:
                return fh.read()
        except (FileNotFoundError, OSError):
            return None

    def _decode(self, start_s: float, end_s: float, ctx: str | None) -> str:
        import time as _time
        bps = self.sample_rate * 2
        raw = self._audio_bytes()
        if raw is None:
            return ""
        a, b = int(start_s * bps), int(end_s * bps)
        if b <= a or b > len(raw):
            return ""
        wav = raw_to_wav_bytes(raw[a:b], self.sample_rate)
        t0 = _time.monotonic()
        text = self.transcriber(wav, ctx or None).strip()
        self.stats["decodes"] += 1
        self.stats["decode_ms_sum"] += (_time.monotonic() - t0) * 1000.0
        return text

    def _emit(self, committed: str, tail: str) -> None:
        text = committed
        if tail:
            text = (committed + " " + join_tail(committed, tail)).strip()
        if not text or text == self.last_text:
            return
        self.last_text = text
        if self._send_armed:
            return  # keep the countdown notice on the pill until it resolves
        self._show(text)

    def _show(self, text: str) -> None:
        shown = text if len(text) <= self.char_limit \
            else "…" + text[-self.char_limit:]
        try:
            self.on_text(shown)
        except Exception:
            pass

    # -- loop --------------------------------------------------------------

    def _loop(self) -> None:
        bps = self.sample_rate * 2
        while not self._stop.wait(self.interval):
            raw = self._audio_bytes()
            if raw is None or len(raw) < self.min_bytes:
                continue
            self.stats["ticks"] += 1
            audio_s = len(raw) / bps
            self.stats["audio_s"] = audio_s
            if self._busy:  # a decode is still running - skip this tick
                continue
            self._busy = True
            try:
                self._tick(raw, audio_s)
            except Exception:
                pass  # preview is best-effort; the final pass is authoritative
            finally:
                self._busy = False

    def _tick(self, raw: bytes, audio_s: float) -> None:
        bps = self.sample_rate * 2
        # -- spoken-send quiet countdown (B7): shares the VAD's tail DSP.
        # Arm once when the user went quiet (>= 0.5 s) right after the
        # send phrase; cancel the moment speech resumes. The daemon owns
        # the actual countdown timer and the stop.
        send_on = (self.send_phrase and self.send_countdown_s > 0
                   and self.on_send_countdown is not None
                   and not self._silence_fired and any(self.committed))
        if send_on:
            from .processing.extra_formats import parse_spoken_send
            if parse_spoken_send(self.last_text,
                                 self.send_phrase).should_send:
                self._phrase_seen_s = audio_s
            tail_raw = raw[int(max(0.0, audio_s - 1.1)
                               * self.sample_rate) * 2:]
            quiet = trailing_silence_s(tail_raw, self.sample_rate)
            if not self._send_armed and quiet >= 0.5 \
                    and self._phrase_seen_s is not None:
                self._send_armed = True
                try:
                    self.on_send_countdown()
                except Exception:
                    pass
            elif quiet < 0.5:
                # active speech: cancels a running countdown AND
                # invalidates a phrase seen earlier (words followed it -
                # it no longer ends the dictation)
                if self._send_armed:
                    self._send_armed = False
                    try:
                        self.on_send_resume()
                    except Exception:
                        pass
                self._phrase_seen_s = None

        # -- VAD early-stop: cheap DSP on the tail, at most one trigger ever.
        # Suppressed while the send countdown is armed: the countdown is
        # the take's finisher then (a late arm after tick lag would
        # otherwise let the plain VAD fire mid-countdown).
        if (self.on_silence is not None and self.vad_silence_s > 0
                and not self._silence_fired and not self._send_armed
                and any(self.committed)):
            lookback = self.vad_silence_s + 0.6
            tail_raw = raw[int(max(0.0, audio_s - lookback)
                               * self.sample_rate) * 2:]
            if (trailing_silence_s(tail_raw, self.sample_rate)
                    >= self.vad_silence_s):
                self._silence_fired = True
                try:
                    self.on_silence()
                except Exception:
                    pass
                return

        # -- one decode per tick, commits first (they must stay in order).
        k = self._next_commit
        commit_end = k * self.hop_s + self.segment_s
        if audio_s >= commit_end:
            ctx = self.committed[-1] if self.committed else None
            text = self._decode(k * self.hop_s, commit_end, ctx)
            self.committed.append(text)
            self._next_commit = k + 2
            self.stats["commits"] += 1
            self.stats["covered_s"] = commit_end
            self._emit(" ".join(t for t in self.committed if t), "")
            return
        if self._silence_fired:
            return
        # -- live tail: newest segment_s slice (partial prefix allowed).
        start = max(0.0, audio_s - self.segment_s)
        if audio_s - start < max(0.5, min(self.min_bytes / bps, 1.0)):
            return
        ctx = " ".join(t for t in self.committed if t)[-200:] or None
        tail = self._decode(start, audio_s, ctx)
        self.stats["covered_s"] = max(self.stats["covered_s"], audio_s)
        self._emit(" ".join(t for t in self.committed if t), tail)


def preview_transcriber(cfg: dict, backend, language: str | None
                        ) -> tuple[Callable[[bytes, str | None], str], str] | None:
    """Bytes+context -> text transcriber for the segmented preview engine,
    for whichever backend is loaded and ready. Returns (fn, backend-name) or
    None when the backend has no model loaded yet (preview simply stays off,
    exactly like the legacy faster-whisper-only path)."""
    name = getattr(backend, "name", "?") if backend is not None else None
    model = getattr(backend, "_model", None)
    lang = None if language in (None, "", "auto") else language

    if name == "faster-whisper" and model is not None:
        def fw(wav: bytes, ctx: str | None) -> str:
            segments, _ = model.transcribe(
                io.BytesIO(wav), language=lang, initial_prompt=ctx or None,
                beam_size=1, condition_on_previous_text=False,
                without_timestamps=True)
            return " ".join(s.text.strip() for s in segments if s.text.strip())
        return fw, name

    if name == "whisper-torch" and model is not None:
        def tw(wav: bytes, ctx: str | None) -> str:
            result = model.transcribe(io.BytesIO(wav), language=lang,
                                      initial_prompt=ctx or None,
                                      beam_size=1,
                                      condition_on_previous_text=False)
            return " ".join(s.text.strip() for s in result.get("segments", [])
                            if s.text.strip())
        return tw, name

    if name == "parakeet" and getattr(backend, "_decoder", None) is not None:
        import numpy as np
        from .backends.parakeet_onnx import SAMPLE_RATE, TAIL_PAD_S, detokenize

        def pk(wav: bytes, ctx: str | None) -> str:  # ctx unused: no prompt concept
            import wave
            with wave.open(io.BytesIO(wav), "rb") as w:
                raw = w.readframes(w.getnframes())
            samples = (np.frombuffer(raw[: (len(raw) // 2) * 2], dtype="<i2")
                       .astype(np.float32) / 32768.0)
            padded = np.concatenate(
                [samples, np.zeros(int(TAIL_PAD_S * SAMPLE_RATE), np.float32)])
            feats = backend._featurizer(padded)
            if backend._normalize_type == "per_feature":
                feats = ((feats - feats.mean(axis=0, keepdims=True))
                         / (feats.std(axis=0, keepdims=True) + 1e-5))
            ids = backend._decoder.run(feats)
            return detokenize(ids, backend._id2tok)
        return pk, name

    if name == "whisper.cpp":
        binary = getattr(backend, "binary", None)
        model_path = getattr(backend, "model", None)
        if binary and model_path:  # constructor validated both already

            def wc(wav: bytes, ctx: str | None) -> str:  # no prompt flag: keep v1 simple
                import subprocess
                import tempfile
                import os
                fd, tmp = tempfile.mkstemp(suffix=".wav")
                try:
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(wav)
                    args = [binary, "-m", model_path, "-f", tmp, "-nt", "-np"]
                    if lang:
                        args += ["-l", lang]
                    proc = subprocess.run(args, capture_output=True, text=True,
                                          timeout=60)
                    return " ".join(l.strip() for l in proc.stdout.splitlines()
                                    if l.strip())
                finally:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
            return wc, name

    return None


# ---------------------------------------------------------------------------
# Displays
# ---------------------------------------------------------------------------

class NotifyPreview:
    """Replaceable desktop notification (notify-send -r reuses one bubble)."""

    def __init__(self, timeout_ms: int = 2000):
        import shutil
        self.supported = bool(shutil.which("notify-send"))
        self.timeout_ms = timeout_ms
        self._id: int | None = None

    def start(self) -> None:  # overlay parity: no window to map
        pass

    def set_state(self, state: str) -> None:  # no processing visual
        pass

    def show(self, text: str) -> None:
        if not self.supported:
            return
        import subprocess
        args = ["notify-send", "-a", "SayItErmano", "-t", str(self.timeout_ms)]
        if self._id is not None:
            args += ["-r", str(self._id), "-e"]
        args += [f"● {text}"]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=3)
            out = (proc.stdout or "").strip()
            if out.isdigit():
                self._id = int(out)
        except Exception:
            pass

    def close(self) -> None:
        if self._id is not None and self.supported:
            import subprocess
            try:
                subprocess.run(["notify-send", "-a", "SayItErmano", "-r",
                                str(self._id), "-t", "1", " "], timeout=3)
            except Exception:
                pass
            self._id = None


# The X11 pill overlay (Mac-style BottomOverlayView port) lives in
# .overlay.FluidOverlay; this module keeps the engine + notification fallback.
