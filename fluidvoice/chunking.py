"""Chunked file transcription (plan P3): a long input is converted once,
sliced into ten-minute overlapping WAV chunks, transcribed sequentially,
and reconciled into one `Transcript` (the P1 seam).

Constants (documented behavior, not knobs):

- `CHUNK_SECONDS` (600): ten-minute decode units — long enough that
  per-chunk model overhead is amortized, short enough that a single
  decode stays inside backend memory/time budgets.
- `CHUNK_OVERLAP_SECONDS` (1.5): the 1–2 s guard band so a word onset
  at a chunk boundary is fully inside at least one chunk.
- `MAX_TOTAL_SECONDS` (6 h): disk/temp safety bound on the decoded
  audio (16 kHz mono s16le ≈ 115 MB/h ⇒ ≈ 690 MB peak temp). Enforced
  with `AudioTooLargeError`, never a hang; `transcribe_long(limit_s=...)`
  overrides it (tests).

The single-active-job guarantee is preserved by construction: chunks are
transcribed sequentially in the calling thread, and callers (daemon
`transcribe`, CLI one-shot) already serialize behind their busy gates.
No new progress protocol surface: per-chunk lines go to the house log.
"""
from __future__ import annotations

import shutil
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .audio_utils import AudioFormatError, ensure_wav
from .backends.base import Transcript, TranscriptSegment
from .pipeline import log

CHUNK_SECONDS = 600.0
CHUNK_OVERLAP_SECONDS = 1.5
MAX_TOTAL_SECONDS = 6.0 * 3600.0

# Sub-half-second remainders are already covered by the previous chunk's
# tail (600 s chunks overlap 1.5 s); don't emit degenerate slices.
_MIN_TAIL_SECONDS = 0.5
# whisper.cpp asserts on sub-1s inputs — pad the (rare) short tail chunk.
_MIN_CHUNK_SECONDS = 1.0


def _fmt_seconds(s: float) -> str:
    return f"{s / 3600:.1f} h" if s >= 3600 else f"{s / 60:.1f} min"


class AudioTooLargeError(RuntimeError):
    """Decoded audio exceeds the disk/temp safety bound."""


@dataclass(frozen=True)
class Chunk:
    """One WAV slice: `start`/`end` are global seconds in the source."""

    path: Path
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def _wave_duration(path: Path) -> float | None:
    """Duration of a RIFF/WAVE file, or None when not one."""
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate() or 16000
            return wf.getnframes() / float(rate)
    except Exception:
        return None


def _probe_duration(path: Path) -> float | None:
    """Best-effort duration of any decodable input (no conversion).

    WAV via the stdlib; everything else via PyAV (the already-present
    faster-whisper dependency); ffprobe as the last resort."""
    dur = _wave_duration(path)
    if dur is not None:
        return dur
    try:
        import av  # deferred; see audio_utils._pyav_decodable
        with av.open(str(path)) as container:
            d = float(container.duration) / 1e6  # microseconds
            if d > 0:
                return d
    except Exception:
        pass
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        import subprocess
        try:
            out = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, timeout=10)
            if out.returncode == 0:
                return float(out.stdout.strip())
        except Exception:
            pass
    return None


def _ensure_slicable_wav(path: Path, dest_dir: Path, force: bool) -> Path:
    """One conversion at most: reuse `ensure_wav` (PyAV passthrough or one
    ffmpeg run), then force a real WAV only if the passthrough result is
    not RIFF (e.g. a .flac that backends read directly)."""
    wav = ensure_wav(path, dest_dir=dest_dir, force=force)
    if _wave_duration(wav) is not None:
        return wav
    return ensure_wav(wav, dest_dir=dest_dir, force=True)


def slice_wav(wav: Path, dest_dir: Path,
              chunk_s: float = CHUNK_SECONDS,
              overlap_s: float = CHUNK_OVERLAP_SECONDS) -> list[Chunk]:
    """Slice one WAV into chunk files with overlap; pure stdlib (no ffmpeg).

    Chunk k starts at k*(chunk_s - overlap_s) and runs chunk_s (the last
    is clipped to the source end). A final tail shorter than
    `_MIN_TAIL_SECONDS` is folded into the previous chunk (already
    covered); with the current constants every emitted chunk is at
    least `overlap_s` long, but a defensive zero pad to
    `_MIN_CHUNK_SECONDS` keeps whisper.cpp's <1s assertion from ever
    firing if the constants change."""
    with wave.open(str(wav), "rb") as wf:
        params = wf.getparams()
        rate = wf.getframerate() or 16000
        duration = wf.getnframes() / float(rate)
    step = chunk_s - overlap_s
    dest_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[Chunk] = []
    start = 0.0
    while start < duration:
        end = min(start + chunk_s, duration)
        if end - start < _MIN_TAIL_SECONDS and chunks:
            break  # degenerate remainder; previous chunk covers it
        out = dest_dir / f"chunk-{len(chunks):03d}.wav"
        pad = max(0.0, _MIN_CHUNK_SECONDS - (end - start))
        with wave.open(str(wav), "rb") as src, \
                wave.open(str(out), "wb") as dst:
            dst.setparams(params)
            src.setpos(int(round(start * rate)))
            want = int(round((end - start) * rate))
            dst.writeframes(src.readframes(want))
            if pad > 0:
                dst.writeframes(b"\0" * int(round(pad * rate))
                                * params.sampwidth * params.nchannels)
        chunks.append(Chunk(path=out, start=start, end=end))
        if end >= duration:
            break
        start += step
    return chunks


def _norm_text(text: str) -> str:
    """Conservative match key: case and whitespace only — anything looser
    risks deleting real speech at boundaries."""
    return " ".join(text.casefold().split())


def merge_transcripts(chunks: list[Chunk], results: list[Transcript],
                      total_duration: float,
                      overlap_s: float = CHUNK_OVERLAP_SECONDS) -> Transcript:
    """Reconcile chunk-local transcripts into one global `Transcript`.

    Dedup rule (conservative, documented): at each boundary the chunks
    overlap in [next.start, prev.end]; a later-chunk segment is dropped
    only when its normalized text exactly equals an earlier-chunk
    segment's AND their global starts agree within `2*overlap_s` — i.e.
    both decoders transcribed the same utterance of the same audio
    (either copy may sit just outside the 1.5 s window: decoders drift).
    Near-but-not-equal text is always kept (better a visible double than
    a silent deletion)."""
    assert len(chunks) == len(results)
    per_chunk: list[list[TranscriptSegment]] = []
    for chunk, result in zip(chunks, results):
        per_chunk.append([
            TranscriptSegment(start=chunk.start + s.start,
                              end=chunk.start + s.end,
                              text=s.text, avg_logprob=s.avg_logprob,
                              no_speech_prob=s.no_speech_prob)
            for s in result.segments if _norm_text(s.text)])

    dropped = set()  # (chunk_idx, seg_idx) removed as boundary duplicates
    slack = 2.0 * overlap_s
    for i in range(len(per_chunk) - 1):
        s_next, e_prev = chunks[i + 1].start, chunks[i].end
        for bi, bseg in enumerate(per_chunk[i + 1]):
            if bseg.start > e_prev + slack:
                continue  # nowhere near the overlap window
            for ai, aseg in enumerate(per_chunk[i]):
                if aseg.start < s_next - slack:
                    continue  # earlier segment outside the overlap window
                if _norm_text(aseg.text) == _norm_text(bseg.text) \
                        and abs(aseg.start - bseg.start) <= slack:
                    dropped.add((i + 1, bi))
                    break

    kept: list[TranscriptSegment] = []
    for ci, segs in enumerate(per_chunk):
        for si, seg in enumerate(segs):
            if (ci, si) not in dropped:
                kept.append(seg)
    kept.sort(key=lambda s: s.start)  # stable: chunk order breaks ties

    text = " ".join(s.text.strip() for s in kept if s.text.strip())
    if not kept:  # no segments (e.g. whisper.cpp): join chunk texts
        text = " ".join(r.text.strip() for r in results if r.text.strip())
    language = next((r.language for r in results if r.language), None)
    return Transcript(text=text, language=language, duration=total_duration,
                      segments=tuple(kept))


def transcribe_long(backend, path: Path, language: str | None = None, *,
                    force_whisper_cpp: bool = False,
                    log_fn: Callable[[str], None] = log,
                    limit_s: float | None = None) -> Transcript:
    """One file -> one `Transcript`, chunked when the audio is long.

    Short inputs (<= `CHUNK_SECONDS`) keep the exact pre-P3 behavior:
    one `ensure_wav` passthrough/convert + one `transcribe()` call. Long
    inputs convert once, slice with overlap, transcribe sequentially,
    and merge. Raises `AudioTooLargeError` past the safety bound (before
    any conversion or decode) and `AudioFormatError` on undecodable
    input; every temp artifact is removed on all exits."""
    path = Path(path)
    limit = MAX_TOTAL_SECONDS if limit_s is None else limit_s
    duration = _probe_duration(path)
    if duration is not None and duration > limit:
        raise AudioTooLargeError(
            f"file too long ({_fmt_seconds(duration)} of audio; limit is "
            f"{_fmt_seconds(limit)} decoded — split the file first)")

    if duration is not None and duration <= CHUNK_SECONDS:
        wav = ensure_wav(path, force=force_whisper_cpp)
        try:
            return Transcript.of(backend.transcribe(wav, language) or {})
        finally:
            if wav != path:
                shutil.rmtree(wav.parent, ignore_errors=True)

    workdir = Path(tempfile.mkdtemp(prefix="fluidvoice-chunks-"))
    try:
        wav = _ensure_slicable_wav(path, workdir, force=force_whisper_cpp)
        total = _wave_duration(wav)
        if total is None:
            raise AudioFormatError(f"cannot slice '{path.name}': not a WAV "
                                   "after conversion")
        if total > limit:  # probe missed it; the decoded truth decides
            raise AudioTooLargeError(
                f"file too long ({_fmt_seconds(total)} of audio; limit is "
                f"{_fmt_seconds(limit)} decoded — split the file first)")
        chunks = slice_wav(wav, workdir)
        results: list[Transcript] = []
        for i, chunk in enumerate(chunks):
            log_fn(f"chunk {i + 1}/{len(chunks)} "
                   f"[{chunk.start:.0f}s+{chunk.duration:.0f}s]")
            results.append(Transcript.of(backend.transcribe(chunk.path, language)
                                 or {}))
        if len(chunks) == 1:  # probe overestimated; behave like v1
            return results[0]
        return merge_transcripts(chunks, results, total)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
