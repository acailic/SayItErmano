"""Small audio helpers (silence gate, mirroring FluidVoice's thresholds)."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

# Input formats verified to decode via PyAV 18.1.0 / ffmpeg 6.1.1
# (specs/eaed8a8c_transcribe-formats-json.md). Other extensions are still
# attempted (PyAV probe + ffmpeg fallback), but only these are advertised.
SUPPORTED_AUDIO_EXTS = frozenset({
    ".wav", ".flac", ".mp3", ".opus", ".oga", ".ogg",
    ".m4a", ".aac", ".wma", ".aiff", ".aif", ".webm",
})


class AudioFormatError(RuntimeError):
    """Input cannot be turned into something decodable."""


def _pyav_decodable(path: Path) -> bool:
    """True when PyAV opens the file and it has an audio stream."""
    try:
        import av  # deferred: faster-whisper dependency, always present
        with av.open(str(path)) as container:
            return any(s.type == "audio" for s in container.streams)
    except Exception:
        return False


def ensure_wav(path: Path, dest_dir: Path | None = None, force: bool = False) -> Path:
    """Return a decodable audio path; convert to 16 kHz mono WAV only when needed.

    - `.wav` inputs pass through untouched (no subprocess) unless ``force``.
    - Other PyAV-decodable formats pass through unless ``force`` (whisper.cpp
      only reliably reads WAV).
    - Anything else is converted with ffmpeg to ``dest_dir/<stem>.16k.wav``
      (``dest_dir`` defaults to a fresh tempdir the *caller* must clean up,
      only when the returned path differs from ``path``).
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".wav" and not force:
        return path
    if not force and suffix != ".wav" and _pyav_decodable(path):
        return path
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        if suffix == ".wav":  # forced re-encode of a WAV with no ffmpeg: try as-is
            return path
        raise AudioFormatError(
            f"cannot decode '{path.name}' ({suffix}): format not directly "
            "decodable and ffmpeg is not installed - install ffmpeg "
            "(e.g. `sudo apt install ffmpeg`) or convert to 16 kHz mono "
            "WAV yourself")
    out_dir = dest_dir if dest_dir is not None else Path(
        tempfile.mkdtemp(prefix="fluidvoice-wav-"))
    out = out_dir / f"{path.stem}.16k.wav"
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-v", "error", "-i", str(path),
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y", str(out)],
        capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        raise AudioFormatError(
            f"ffmpeg failed to convert {path.name}: {proc.stderr.strip()[:300]}")
    return out


@dataclass
class AudioStats:
    peak: float
    rms: float
    max_frame_rms: float  # 20 ms frames


def wav_stats(path: str) -> AudioStats:
    with wave.open(str(path), "rb") as wf:
        n = wf.getnframes()
        rate = wf.getframerate() or 16000
        raw = wf.readframes(n)
    import array
    samples = array.array("h")
    samples.frombytes(raw[: (len(raw) // 2) * 2])
    if not samples:
        return AudioStats(0.0, 0.0, 0.0)
    floats = [s / 32768.0 for s in samples]
    peak = max(abs(v) for v in floats)
    rms = (sum(v * v for v in floats) / len(floats)) ** 0.5
    frame = max(1, int(rate * 0.02))
    max_frame = 0.0
    for i in range(0, len(floats), frame):
        chunk = floats[i : i + frame]
        if chunk:
            fr = (sum(v * v for v in chunk) / len(chunk)) ** 0.5
            max_frame = max(max_frame, fr)
    return AudioStats(peak=peak, rms=rms, max_frame_rms=max_frame)


def is_silent(path: str) -> bool:
    """FluidVoice's short-recording silence gate."""
    stats = wav_stats(path)
    return stats.peak < 0.01 and stats.rms < 0.002 and stats.max_frame_rms < 0.0045


def is_digital_silence(path: str) -> bool:
    """True when every sample byte is zero - a dead or wedged capture
    path streaming zeros (2026-09-10: PipeWire delivered exact silence
    from a healthy webcam mic), which is not quiet speech."""
    import wave as _wave
    try:
        with _wave.open(str(path), "rb") as wf:
            while True:
                chunk = wf.readframes(16000)
                if not chunk:
                    break
                if chunk.count(b"\x00") != len(chunk):
                    return False
        return True
    except Exception:
        return False


def duration_seconds(path: str) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate() or 16000)


def raw_to_wav_bytes(raw: bytes, rate: int = 16000, channels: int = 1,
                     width: int = 2) -> bytes:
    """Wrap headerless s16le PCM in a WAV container (in memory)."""
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(raw)
    return buf.getvalue()


def raw_to_wav_file(raw_path, wav_path, rate: int = 16000) -> None:
    with open(raw_path, "rb") as fh:
        data = fh.read()
    with open(wav_path, "wb") as fh:
        fh.write(raw_to_wav_bytes(data, rate))


def pad_wav(path, rate: int = 16000, frame_width: int = 2) -> None:
    """Zero-pad sub-1s audio to exactly `rate` samples (16000). Upstream does
    this unconditionally - whisper.cpp asserts on <1s inputs."""
    import wave as _wave
    with _wave.open(str(path), "rb") as wf:
        n = wf.getnframes()
        params = wf.getparams()
        data = wf.readframes(n)
    if n >= rate or params.nframes == 0:
        return
    with _wave.open(str(path), "wb") as wf_out:
        wf_out.setparams(params)
        wf_out.writeframes(data)
        wf_out.writeframes(b"\x00" * (rate - n) * params.nchannels * frame_width)
