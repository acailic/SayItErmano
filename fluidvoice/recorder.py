"""Microphone recording via PipeWire (`pw-record`) or PulseAudio (`parecord`).

Produces 16 kHz mono s16 WAV files, matching FluidVoice's audio format.
"""
from __future__ import annotations

import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path


class RecorderError(RuntimeError):
    pass


def pick_command(prefer: str = "auto") -> tuple[str, list[str]]:
    """Return (argv-prefix, wav-arg-style) for the first available recorder."""
    candidates: list[str]
    if prefer == "pw-record":
        candidates = ["pw-record"]
    elif prefer == "parecord":
        candidates = ["parecord"]
    else:
        candidates = ["pw-record", "parecord"]
    for cmd in candidates:
        if shutil.which(cmd):
            return cmd, []
    raise RecorderError("no recorder found: install pipewire (pw-record) or pulseaudio-utils (parecord)")


# Start probe: poll for early death / first PCM instead of sleeping a fixed
# 350 ms (toggle latency was pinned to that sleep; spawn-to-first-PCM is
# ~90 ms for pw-record). 2048 bytes = 1024 frames (~64 ms of audio), the
# same partial-write threshold the daemon's first_pcm_timeout uses.
PROBE_SECONDS = 0.35
PROBE_TICK_S = 0.01


class Recorder:
    """Record 16 kHz mono s16 WAV to a file, started/stopped around a hotkey."""

    def __init__(self, command: str = "auto", device: str = "", sample_rate: int = 16000):
        self.command = command
        self.device = device
        self.sample_rate = sample_rate
        self.proc: subprocess.Popen | None = None
        self._path: Path | None = None
        self._raw_path: Path | None = None
        self._started_at = 0.0

    @property
    def path(self) -> Path | None:
        return self._path

    def start(self, path: Path) -> None:
        if self.proc is not None:
            raise RecorderError("recorder already running")
        cmd, argv = pick_command(self.command)
        # Record headerless raw PCM: the file stays readable while growing
        # (live preview) and gets a proper WAV header at stop.
        raw_path = path.with_suffix(".raw")
        if cmd == "pw-record":
            args = [cmd, "--rate", str(self.sample_rate), "--channels", "1", "--format", "s16"]
            if self.device:
                args += ["--target", self.device]
        else:
            args = [cmd, f"--rate={self.sample_rate}", "--channels=1", "--format=s16le",
                    "--raw"]
            if self.device:
                args += ["--device", self.device]
        args += [str(raw_path)]
        self._path = path
        self._raw_path = raw_path
        self._started_at = time.monotonic()
        self.proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        # Fail fast if the recorder dies immediately (bad device, no mic, ...),
        # but return as soon as PCM is flowing. If it stays alive without PCM
        # for the whole probe window, proceed anyway: a live-but-silent source
        # (muted mic, wrong device) is the daemon first_pcm_timeout watchdog's
        # job, not ours.
        probe = Path(raw_path)
        for _ in range(int(PROBE_SECONDS / PROBE_TICK_S)):
            if self.proc.poll() is not None:
                stderr = (self.proc.stderr.read() or b"").decode(errors="replace").strip()
                self.proc = None
                raise RecorderError(f"{cmd} exited immediately: {stderr}")
            try:
                if probe.stat().st_size > 2048:
                    break
            except OSError:
                pass
            time.sleep(PROBE_TICK_S)
        # Drain stderr for the rest of the session: a chatty recorder would
        # otherwise fill the 64 KB pipe buffer and silently block mid-recording.
        proc = self.proc

        def _drain(p: subprocess.Popen) -> None:
            try:
                p.stderr.read()
            except Exception:
                pass

        threading.Thread(target=_drain, args=(proc,), daemon=True).start()

    @property
    def raw_path(self) -> Path | None:
        return self._raw_path

    def stop(self, timeout: float = 2.5) -> Path | None:
        """Stop recording, wrap the raw PCM into a WAV, return its path."""
        proc, path, raw = self.proc, self._path, self._raw_path
        self.proc, self._path, self._raw_path = None, None, None
        if proc is None:
            return path
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        if raw is not None and raw.exists():
            from .audio_utils import raw_to_wav_file
            try:
                raw_to_wav_file(raw, path, self.sample_rate)
            finally:
                raw.unlink(missing_ok=True)
        return path

    def cancel(self) -> None:
        path = self.stop()
        if path and path.exists():
            path.unlink(missing_ok=True)
        if self._raw_path is not None:
            self._raw_path.unlink(missing_ok=True)

    def elapsed(self) -> float:
        return time.monotonic() - self._started_at if self.proc else 0.0
