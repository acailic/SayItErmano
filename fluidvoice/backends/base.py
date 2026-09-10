"""The speech-backend seam (plan P1.1): typed capabilities and results.

Every speech adapter implements `SpeechBackend`: an identity `name`, a
frozen `BackendCapabilities` declaration (what the adapter can actually
do, verified against its code), and a `transcribe()` returning a
`Transcript`. Consumers (pipeline, daemon, preview, CLI) read typed
fields instead of duck-typed flags and result-dictionary keys;
serialization to the historical dictionary/JSON shapes happens only at
the CLI/control/MCP edges via `to_dict()`.

Compatibility bridge (behavior preservation is the bar for P1.1):

- `capabilities_of()` resolves a backend's capabilities, falling back to
  the legacy class attributes (`selects_language`,
  `surfaces_detected_language`) so pre-seam fakes in tests and third-party
  duck-typed adapters keep working unchanged.
- `Transcript.of()` normalizes whatever `transcribe()` returned — a typed
  `Transcript` or a legacy result dictionary — into a `Transcript`.
- `Transcript` is also a read-only `Mapping` over its serialized form, so
  legacy ``result["text"]`` / ``result.get("language")`` sites keep
  working during the migration. New code must use the typed fields.

Internal types only: nothing here is a public API (plan §"Public
Interfaces and Migration" — backend result and capability types stay
internal until serialized at the existing edges).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class BackendCapabilities:
    """What one speech adapter truthfully supports.

    Declared by each backend as a class attribute; verified per adapter
    by tests/test_backend_contract.py. All flags default to False — an
    adapter only claims what its code path actually does.

    - supports_language_select: `transcribe(language=...)` pins the
      decode language (the hallucination guard's auto-retry prerequisite).
    - supports_language_detect: under ``auto`` the transcript carries the
      DETECTED language (the wrong-language whitelist guard reads it);
      adapters that only echo the requested hint (or detect without
      surfacing it) declare False.
    - supports_hotwords: model.hotwords biases decoding (native decoder
      hotwords or the closest hint mechanism the adapter forwards).
    - supports_streaming: a genuine incremental streaming interface.
      False for every adapter today: live preview is built on segmented
      BATCH re-decodes of the growing buffer, not a streaming decode
      (true streaming waits on a real adapter, plan P3).
    - supports_segments: transcripts carry start/end/text segments.
    - supports_confidence: segments carry confidence signals
      (avg_logprob; some adapters also no_speech_prob).
    - supports_alternatives: n-best alternatives. False for every
      adapter today (plan P3: blocked until two adapters expose genuine
      alternatives).
    """

    supports_language_select: bool = False
    supports_language_detect: bool = False
    supports_hotwords: bool = False
    supports_streaming: bool = False
    supports_segments: bool = False
    supports_confidence: bool = False
    supports_alternatives: bool = False


@dataclass(frozen=True)
class TranscriptSegment:
    """One transcript segment. `avg_logprob`/`no_speech_prob` (whisper
    confidence signals) are optional: absent (None) on adapters without
    `supports_confidence`. `to_dict()` omits None optionals so legacy
    three-key segment dictionaries serialize unchanged."""

    start: float
    end: float
    text: str
    avg_logprob: float | None = None
    no_speech_prob: float | None = None

    @classmethod
    def from_dict(cls, d: Any) -> TranscriptSegment:
        """Tolerant parse: missing optional fields and unknown extra keys
        (e.g. a remote endpoint's verbose_json segments) are accepted."""
        if not isinstance(d, Mapping):
            d = {}
        start = d.get("start", 0.0)
        end = d.get("end", 0.0)
        return cls(
            start=float(start) if isinstance(start, (int, float)) else 0.0,
            end=float(end) if isinstance(end, (int, float)) else 0.0,
            text=str(d.get("text") or ""),
            avg_logprob=cls._num(d.get("avg_logprob")),
            no_speech_prob=cls._num(d.get("no_speech_prob")),
        )

    @staticmethod
    def _num(v: Any) -> float | None:
        # bool is an int subclass - never a confidence value
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"start": self.start, "end": self.end,
                               "text": self.text}
        if self.avg_logprob is not None:
            out["avg_logprob"] = self.avg_logprob
        if self.no_speech_prob is not None:
            out["no_speech_prob"] = self.no_speech_prob
        return out


@dataclass(frozen=True)
class Transcript(Mapping):
    """One finished transcription, typed at the seam.

    Kept lean deliberately (plan P1.1): text, language (detected or
    echoed hint; None when the adapter cannot say), duration (seconds;
    None when the adapter cannot say), and segments (empty tuple when
    the adapter has no `supports_segments`). Heavier per-backend extras
    (alternatives, speakers, real-time factor) join through new optional
    fields when a real adapter needs them — not before.

    Also a read-only Mapping over the serialized form (legacy
    ``result["text"]`` / ``result.get("language")`` compat).
    """

    text: str
    language: str | None = None
    duration: float | None = None
    segments: tuple[TranscriptSegment, ...] = ()

    @classmethod
    def of(cls, result: Transcript | Mapping[str, Any]) -> Transcript:
        """Normalize a `transcribe()` result: Transcript passes through,
        a legacy result dictionary is parsed. The one conversion point
        consumers need (handles pre-seam fakes unchanged)."""
        if isinstance(result, Transcript):
            return result
        if isinstance(result, Mapping):
            return cls.from_dict(result)
        raise TypeError(
            f"transcribe() must return Transcript or dict, "
            f"got {type(result).__name__}")

    @classmethod
    def from_dict(cls, d: Any) -> Transcript:
        """Tolerant parse (missing keys, wrong-typed optionals and
        non-dict segment entries are dropped, never fatal)."""
        if not isinstance(d, Mapping):
            d = {}
        language = d.get("language")
        duration = d.get("duration")
        segments = d.get("segments")
        segs = tuple(TranscriptSegment.from_dict(s) for s in segments
                     if isinstance(s, Mapping)) \
            if isinstance(segments, (list, tuple)) else ()
        return cls(
            text=str(d.get("text") or ""),
            language=str(language) if language not in (None, "") else None,
            duration=float(duration)
            if isinstance(duration, (int, float))
            and not isinstance(duration, bool) else None,
            segments=segs,
        )

    def to_dict(self) -> dict[str, Any]:
        """The historical result-dictionary shape (always the four keys;
        segment optionals omitted when absent) — the serialization used
        at the CLI/control/MCP edges. Byte-compatible with pre-seam
        output where tests assert it."""
        return {"text": self.text, "language": self.language,
                "duration": self.duration,
                "segments": [s.to_dict() for s in self.segments]}

    def to_plain_text(self) -> str:
        """Just the raw transcript text (edges that print/store text)."""
        return self.text

    # -- legacy Mapping shim (compat; new code uses the typed fields) ----

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


@runtime_checkable
class SpeechBackend(Protocol):
    """The adapter contract every speech backend implements.

    - `name`: identity used in status/doctor/preview wiring (stable,
      human-readable, e.g. "faster-whisper").
    - `capabilities`: the adapter's truthful BackendCapabilities.
    - `transcribe()`: one WAV file -> one Transcript. `language` is the
      resolved hint ("auto"/None = detect); honored only when
      `supports_language_select`.
    - `warmup()`/`close()`: optional-in-spirit lifecycle (adapters with
      nothing to preload or tear down provide no-ops; call sites stay
      best-effort tolerant of duck-typed adapters).
    """

    name: str
    capabilities: BackendCapabilities

    def transcribe(self, wav_path: Path,
                   language: str | None = None) -> Transcript: ...

    def warmup(self) -> None: ...

    def close(self) -> None: ...


def capabilities_of(backend: Any) -> BackendCapabilities:
    """Resolve a backend's capabilities through the seam.

    Prefers the typed `capabilities` attribute; falls back to the legacy
    duck-typed flags (`selects_language`, `surfaces_detected_language`)
    so pre-seam fakes and duck-typed adapters declare nothing new and
    keep their old behavior (absent flags = capability off). None is
    tolerated (an unloaded daemon slot)."""
    caps = getattr(backend, "capabilities", None)
    if isinstance(caps, BackendCapabilities):
        return caps
    return BackendCapabilities(
        supports_language_select=bool(
            getattr(backend, "selects_language", False)),
        supports_language_detect=bool(
            getattr(backend, "surfaces_detected_language", False)),
    )
