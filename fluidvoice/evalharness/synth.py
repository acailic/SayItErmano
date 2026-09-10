"""Deterministic synthetic wav synthesis for evaluation fixtures.

Fixtures are *regenerated*, never committed: every case in the committed
corpus carries an optional ``synth`` recipe, and the harness materializes
the wav on first use. This keeps the repo free of binary assets (the
global ``*.wav`` gitignore stays meaningful), guarantees fixtures are
redistributable by construction (they contain a tone, not speech), and
makes runs reproducible — the same recipe always yields identical bytes.

Stdlib only (``wave`` + ``struct`` + ``math``), mirroring the audio
synthesis tests/test_daemon.py already uses. 16-bit PCM mono.
"""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from .manifest import SYNTH_KINDS, Case, SynthSpec  # noqa: F401 (re-export)

_RATE = 16_000
_WIDTH = 2          # bytes per sample (s16)
_AMP = 12_000       # ~-4 dBFS, comfortably above every silence detector
_TONE_HZ = 440.0
_CHIRP_START_HZ = 300.0
_CHIRP_END_HZ = 1200.0


def _samples(spec: SynthSpec) -> list[int]:
    """Deterministic sample list for a recipe (pure function of i)."""
    rate = spec.rate
    if spec.kind == "silence":
        return [0] * int(rate * spec.seconds)
    out = []
    for i in range(int(rate * spec.seconds)):
        t = i / rate
        if spec.kind == "tone":
            hz = _TONE_HZ
        else:  # chirp: linear sweep start->end over the clip
            hz = _CHIRP_START_HZ + (_CHIRP_END_HZ - _CHIRP_START_HZ) * (
                t / spec.seconds if spec.seconds else 0.0)
        out.append(int(_AMP * math.sin(2 * math.pi * hz * t)))
    return out


def synth_wav(path: Path, spec: SynthSpec) -> Path:
    """Write the synthetic wav for ``spec`` to ``path`` (parent created).

    Same spec -> identical bytes: generation is a pure function of the
    recipe, so fixtures regenerate identically on any machine.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(_WIDTH)
        wf.setframerate(spec.rate)
        frames = bytearray()
        pack_into = struct.Struct("<h").pack
        for v in _samples(spec):
            frames += pack_into(v)
        wf.writeframes(bytes(frames))
    return path


def wav_duration_seconds(path: Path) -> float | None:
    """Play time of a wav from its header, None when it isn't a wav.

    Reads only the RIFF header (no decode); non-wav audio returns None
    and RTF/latency aggregates simply exclude such cases.
    """
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except (wave.Error, OSError, ZeroDivisionError):
        return None


def ensure_case_audio(case: Case, *, allow_synth: bool = True) -> Path:
    """Return the case's audio path, synthesizing it when needed.

    Missing audio + a synth recipe + synthesis allowed -> generate.
    Missing audio + no recipe (or synthesis disabled) -> FileNotFoundError:
    the harness never invents audio for a case that declares none.
    """
    if case.audio.exists():
        return case.audio
    if case.synth is not None and allow_synth:
        return synth_wav(case.audio, case.synth)
    what = "synthesis disabled (--no-synth)" if case.synth is not None \
        else "no synth recipe in the manifest"
    raise FileNotFoundError(
        f"case '{case.id}': audio file not found ({case.audio}) and {what}")
