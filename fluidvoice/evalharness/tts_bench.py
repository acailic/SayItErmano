"""TTS decode baseline: real-model WER/latency/hallucination via piper.

The first honest decode-quality measurement with the machine's REAL
speech model, driven by the wave-2 measurement adapters (runner.py /
metrics.py): piper renders a fixed, versioned reference set
(``corpus/tts-reference.toml``, stratified like docs/eval/corpus-spec.md
§4 T1–T6) plus silence/noise negatives to speech audio; the production
speech backend — constructed exactly the way the app constructs it —
transcribes every clip; the adapters score the round trip.

Evidence report: ``docs/eval/decode-baseline-2026-09-11.md``. This is
NOT the human corpus (that brief stays OPEN): single TTS voice, English
only, machine-clean audio — a baseline, clearly caveated there.

Backend note (recorded honestly): the wave-3 brief named the whisper.cpp
ggml model; on this machine no ``whisper-cli`` binary exists, so the
app's own resolution (``backends.load_backend`` with the daemon's
in-memory config mirror) picks **faster-whisper large-v3 (CPU int8)** —
the same backend the production daemon uses for every real dictation
(history.jsonl ``backend`` field). The adapter therefore constructs via
``load_backend`` and reports whatever actually resolves.

Two passes per run:

- **forced** — production mirror: every speech case + every negative,
  language forced to the daemon's ``general.language`` (``en``). This
  is the number users get.
- **auto** (probe) — a fixed short subset + all negatives with language
  ``auto``: language-detection sanity (G5, expected en) and the classic
  auto-detect-on-silence hallucination trigger (G3).

Rerun (``just``-style, from the repo root; ~15–20 min on the baseline
machine — runtime guard: cut with ``--limit`` if a run would exceed
~25 min, minimum 15 utterances):

    /home/nistrator/Documents/github/FluidVoiceLinux/.venv/bin/python \\
        -m fluidvoice.evalharness.tts_bench \\
        --workdir ~/.cache/sayitermano-bench

piper must be importable somewhere runnable (the bench keeps a private
venv: ``~/.cache/sayitermano-bench/venv``; ``--piper`` overrides). The
audio workdir lives OUTSIDE git; generated audio is never committed.
"""
from __future__ import annotations

import json
import math
import os
import random
import shutil
import struct
import subprocess
import time
import tomllib
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .manifest import Case, ManifestError, load_manifest
from .runner import Transcriber, run_eval

# ── fixed paths / constants ────────────────────────────────────────────────

REFERENCE_PATH = Path(__file__).resolve().parent / "corpus" / \
    "tts-reference.toml"

DEFAULT_WORKDIR = Path.home() / ".cache" / "sayitermano-bench"
BENCH_VENV_PIPER = DEFAULT_WORKDIR / "venv" / "bin" / "piper"
DEFAULT_VOICE = Path.home() / ".piper" / "models" / \
    "en_US-lessac-medium.onnx"

TARGET_RATE = 16_000          # production byte-format (corpus-spec §5)
_SPEAKER_LABEL = "piper-en_US-lessac-medium"     # honest provenance
_SOURCE_LABEL = ("piper TTS rendering of "
                 "fluidvoice/evalharness/corpus/tts-reference.toml")
_LICENSE_LABEL = "tts-synthetic (generated locally; never committed)"

SPEECH_TAXONOMIES = ("t1-command", "t2-message", "t3-jargon",
                     "t4-names-numbers", "t5-longform", "t6-dev-prompt")
NEGATIVE_KINDS = ("silence", "noise")

# Auto-probe subset (fixed rule): first 2 cases of every short taxonomy
# (T5 long-form excluded — auto detection costs ~2x forced decode).
AUTO_PROBE_TAXONOMIES = ("t1-command", "t2-message", "t3-jargon",
                         "t4-names-numbers", "t6-dev-prompt")
AUTO_PROBE_PER_TAXONOMY = 2

MIN_SPEECH_CASES = 15         # runtime-guard floor (wave-3 brief)

# Low-noise negative level: uniform noise, peak ±700 of ±32767
# (~ -43 dBFS peak, ~ -48 dBFS RMS) — a quiet room floor, not a signal.
NOISE_PEAK = 700
NOISE_SEED = "tts-bench-noise-v1"     # deterministic across runs


# ── reference fixture ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Utterance:
    """One reference speech case (fixture row)."""
    id: str
    taxonomy: str          # SPEECH_TAXONOMIES member
    text: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Negative:
    """One negative (empty-reference) case: silence or low noise."""
    id: str
    kind: str              # NEGATIVE_KINDS member
    seconds: float


def load_reference(path: Path | None = None,
                   ) -> tuple[list[Utterance], list[Negative]]:
    """Parse + validate the reference fixture (fixed and versioned).

    Raises ManifestError on any defect: duplicate ids or texts, unknown
    taxonomy, bad negative kinds, or a speech count outside 25–40 (the
    brief's stratification window — keeps future edits honest).
    """
    path = Path(path or REFERENCE_PATH)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ManifestError(f"cannot read reference fixture {path}: {e}")

    utts: list[Utterance] = []
    for i, row in enumerate(data.get("utterances", [])):
        where = f"{path}: utterances[{i}]: "
        for field in ("id", "taxonomy", "text"):
            v = row.get(field)
            if not isinstance(v, str) or not v.strip():
                raise ManifestError(f"{where}'{field}' must be a "
                                    f"non-empty string, got {v!r}")
        if row["taxonomy"] not in SPEECH_TAXONOMIES:
            raise ManifestError(f"{where}unknown taxonomy "
                                f"{row['taxonomy']!r} (choose from "
                                f"{SPEECH_TAXONOMIES})")
        tags = row.get("tags", [])
        if not isinstance(tags, list) or any(
                not isinstance(t, str) or not t.strip() for t in tags):
            raise ManifestError(f"{where}'tags' must be a list of "
                                f"non-empty strings, got {tags!r}")
        utts.append(Utterance(id=row["id"], taxonomy=row["taxonomy"],
                              text=row["text"], tags=tuple(tags)))

    negs: list[Negative] = []
    for i, row in enumerate(data.get("negatives", [])):
        where = f"{path}: negatives[{i}]: "
        for field in ("id", "kind"):
            v = row.get(field)
            if not isinstance(v, str) or not v.strip():
                raise ManifestError(f"{where}'{field}' must be a "
                                    f"non-empty string, got {v!r}")
        if row["kind"] not in NEGATIVE_KINDS:
            raise ManifestError(f"{where}unknown kind {row['kind']!r} "
                                f"(choose from {NEGATIVE_KINDS})")
        seconds = row.get("seconds")
        if not isinstance(seconds, (int, float)) or not seconds > 0:
            raise ManifestError(f"{where}'seconds' must be > 0, got "
                                f"{seconds!r}")
        negs.append(Negative(id=row["id"], kind=row["kind"],
                             seconds=float(seconds)))

    _validate_reference(path, utts)
    if not negs:
        raise ManifestError(f"{path}: no [[negatives]] (the baseline "
                            "needs silence/noise negatives, corpus-spec §4)")
    return utts, negs


def _validate_reference(path: Path, utts: list[Utterance]) -> None:
    ids = [u.id for u in utts]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ManifestError(f"{path}: duplicate utterance ids: "
                            f"{sorted(dupes)}")
    texts = [u.text.casefold() for u in utts]
    text_dupes = {t for t in texts if texts.count(t) > 1}
    if text_dupes:
        raise ManifestError(f"{path}: duplicate utterance texts: "
                            f"{sorted(text_dupes)}")
    missing = [t for t in SPEECH_TAXONOMIES
               if not any(u.taxonomy == t for u in utts)]
    if missing:
        raise ManifestError(f"{path}: taxonomy {missing[0]!r} has no "
                            "cases (stratification requires all of "
                            f"{SPEECH_TAXONOMIES})")
    if not 25 <= len(utts) <= 40:
        raise ManifestError(f"{path}: {len(utts)} utterances — the "
                            "reference set is 25–40 (brief: wave 3, "
                            "agent J); resize deliberately, not by drift")


def auto_probe_ids(utterances: Sequence[Utterance]) -> list[str]:
    """Fixed auto-probe subset: first AUTO_PROBE_PER_TAXONOMY cases of
    each short taxonomy (AUTO_PROBE_TAXONOMIES), in fixture order."""
    picked: list[str] = []
    for taxonomy in AUTO_PROBE_TAXONOMIES:
        ids = [u.id for u in utterances if u.taxonomy == taxonomy]
        picked.extend(ids[:AUTO_PROBE_PER_TAXONOMY])
    return picked


# ── wav I/O + resampling (stdlib only) ─────────────────────────────────────

def write_wav(path: Path, samples: Sequence[int],
              rate: int = TARGET_RATE) -> Path:
    """16-bit mono PCM wav (parent created)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pack_into = struct.Struct("<h").pack
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(
            pack_into(max(-32768, min(32767, v))) for v in samples))
    return path


def read_wav(path: Path) -> tuple[int, list[int]]:
    """(rate, samples) of a 16-bit mono wav."""
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError(f"{path}: expected mono 16-bit wav "
                             f"(got {wf.getnchannels()}ch/"
                             f"{wf.getsampwidth() * 8}bit)")
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    unpack_from = struct.Struct("<h").unpack_from
    return rate, [unpack_from(frames, i)[0]
                  for i in range(0, len(frames), 2)]


def _sinc(x: float) -> float:
    if x == 0.0:
        return 1.0
    px = math.pi * x
    return math.sin(px) / px


def resample_to_16k(samples: Sequence[int], src_rate: int,
                    taps: int = 12) -> list[int]:
    """Windowed-sinc resampler to 16 kHz (pure stdlib, deterministic).

    Hann-windowed sinc kernel, cutoff at 0.95 × the lower Nyquist —
    the ffmpeg fallback path when ffmpeg is unavailable (the real run
    prefers ffmpeg; both produce 16 kHz mono s16). Pure function of the
    input samples.
    """
    if src_rate == TARGET_RATE:
        return list(samples)
    if src_rate <= 0:
        raise ValueError(f"invalid source rate {src_rate}")
    step = src_rate / TARGET_RATE
    # normalized cutoff (fraction of src Nyquist)
    fc = 0.5 * min(1.0, TARGET_RATE / src_rate) * 0.95
    n_out = int(len(samples) / step)
    out: list[int] = []
    for j in range(n_out):
        t = j * step
        i0 = max(0, int(t) - taps + 1)
        i1 = min(len(samples) - 1, int(t) + taps - 1)
        acc = 0.0
        for k in range(i0, i1 + 1):
            d = k - t
            if abs(d) >= taps:
                continue
            window = 0.5 * (1.0 + math.cos(math.pi * d / taps))
            acc += samples[k] * _sinc(2.0 * fc * d) * window
        out.append(int(round(2.0 * fc * acc)))
    return out


def _ffmpeg_convert(src: Path, dst: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-ar", str(TARGET_RATE), "-ac", "1", "-c:a", "pcm_s16le",
         str(dst)],
        check=True, capture_output=True, timeout=60)


# ── TTS generation (piper) ─────────────────────────────────────────────────

def piper_binary(explicit: str | Path | None = None) -> Path | None:
    """Locate a runnable piper: --piper flag, the bench venv, then PATH.

    Note: ``~/.local/bin/piper`` is a stale shim importing the WRONG
    ``piper`` package (the mouse tool) on this machine — found via the
    bench venv instead; per-utterance failures are recorded, not fatal.
    """
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    if BENCH_VENV_PIPER.is_file():
        return BENCH_VENV_PIPER
    found = shutil.which("piper")
    return Path(found) if found else None


def synth_utterance(piper: Path, voice: Path, text: str, out_path: Path,
                    *, use_ffmpeg: bool = True) -> tuple[Path, float, str]:
    """Render ``text`` with piper → 16 kHz mono s16 wav at ``out_path``.

    Returns (path, duration_s, method) where method records the rate
    conversion ("passthrough" / "ffmpeg(22050->16000)" /
    "sinc(22050->16000)"). Raises on any piper/convert failure — the
    orchestrator records and skips.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw = out_path.with_suffix(".raw.wav")
    try:
        proc = subprocess.run(
            [str(piper), "-m", str(voice), "-f", str(raw)],
            input=text, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0 or not raw.is_file():
            raise RuntimeError(f"piper failed: "
                               f"{(proc.stderr or proc.stdout)[:300]}")
        rate, samples = read_wav(raw)
        if rate == TARGET_RATE:
            write_wav(out_path, samples, rate)
            method = "passthrough"
        elif use_ffmpeg and shutil.which("ffmpeg"):
            _ffmpeg_convert(raw, out_path)
            method = f"ffmpeg({rate}->{TARGET_RATE})"
        else:
            write_wav(out_path, resample_to_16k(samples, rate))
            method = f"sinc({rate}->{TARGET_RATE})"
    finally:
        raw.unlink(missing_ok=True)
    with wave.open(str(out_path), "rb") as wf:
        duration = wf.getnframes() / float(wf.getframerate())
    return out_path, duration, method


def silence_samples(seconds: float, rate: int = TARGET_RATE) -> list[int]:
    return [0] * int(rate * seconds)


def noise_samples(seconds: float, peak: int = NOISE_PEAK,
                  rate: int = TARGET_RATE) -> list[int]:
    """Deterministic low-level noise (seeded; identical every run)."""
    rng = random.Random(NOISE_SEED)
    return [rng.randint(-peak, peak) for _ in range(int(rate * seconds))]


def generate_negatives(negatives: Sequence[Negative],
                       audio_dir: Path) -> list[tuple[Negative, Path]]:
    """Write every negative wav under ``audio_dir`` (16 kHz mono s16)."""
    out: list[tuple[Negative, Path]] = []
    for neg in negatives:
        path = audio_dir / f"{neg.id}.wav"
        if neg.kind == "silence":
            write_wav(path, silence_samples(neg.seconds))
        else:
            write_wav(path, noise_samples(neg.seconds))
        out.append((neg, path))
    return out


# ── transcriber adapter (production construction path) ─────────────────────

def build_backend_config() -> dict[str, Any]:
    """Minimal in-memory config mirroring the daemon's ``[general]`` /
    ``[model]`` sections (values as of 2026-09-11; ~/.config/
    sayit-ermano/config.toml is never read or written here).

    This is the dict the daemon feeds ``backends.load_backend`` — the
    adapter constructs the backend exactly the way the app does.
    """
    return {
        "general": {"language": "en"},        # daemon general.language
        "model": {
            "backend": "auto",               # daemon model.backend
            "name": "large-v3",              # daemon model.name
            "device": "auto",
            "compute": "auto",
            "languages": {},
            "hotwords": [],
        },
    }


def probe_backend_env() -> dict[str, Any]:
    """Cheap availability probes (never instantiates a backend): which
    backend the app's own resolution WOULD pick, and whether the
    whisper.cpp binary exists (the wave-3 brief's named path)."""
    from .. import backends
    cpp = next((name for name in ("whisper-cli", "whisper-cpp",
                                  "whisper.cpp", "whisper-main")
                if shutil.which(name)), None)
    cfg = build_backend_config()
    resolved = backends.resolved_backend_name(cfg)
    model_name = ""
    try:
        model_name = backends.resolve_model_name(
            cfg["model"]["name"])
    except Exception:                        # unknown name: report raw
        model_name = str(cfg["model"]["name"])
    return {
        "config": "in-memory mirror of daemon [model]/[general]; "
                  "no config file reads or writes",
        "resolved_backend": resolved,
        "resolved_model": model_name,
        "whispercpp_binary_on_path": cpp,
        "note": "brief named whisper.cpp; no whisper-cli binary on "
                "this machine, so the app's auto resolution constructs "
                "faster-whisper (the daemon's actual backend)",
    }


def make_transcriber(language: str = "en") -> Transcriber:
    """Factory for the evalharness ``--transcriber module:factory``
    contract: constructs the backend via ``backends.load_backend`` (the
    app's own call), warms it exactly like the daemon's eager warmup,
    and returns the case transcriber.

    ``language``: "en" mirrors production (forced); "auto" runs
    detection (the G5 probe — detected languages are reported).
    """
    from .. import backends
    cfg = build_backend_config()
    cfg["general"]["language"] = language
    backend = backends.load_backend(cfg)
    backend.warmup()        # daemon eager_warmup mirror: load + probe

    def transcribe(audio_path: Path, case: Case) -> dict[str, Any]:
        t0 = time.perf_counter()
        transcript = backend.transcribe(audio_path)
        elapsed = time.perf_counter() - t0
        out: dict[str, Any] = {
            "text": transcript.text,
            "final_latency_s": elapsed,
            "processing_time_s": elapsed,
        }
        if language == "auto":
            out["detected_language"] = transcript.language
        return out

    transcribe.backend = backend       # type: ignore[attr-defined]
    return transcribe


def make_auto_transcriber() -> Transcriber:
    """No-arg factory for the auto-detection probe pass."""
    return make_transcriber(language="auto")


# ── manifest generation ────────────────────────────────────────────────────

def _toml_str(s: str) -> str:
    """TOML basic string (JSON escapes are a valid subset)."""
    return json.dumps(s, ensure_ascii=True)


def write_manifest(path: Path, speech: Sequence[Utterance],
                   negatives: Sequence[tuple[Negative, Path]],
                   audio_of: dict[str, Path], *, suffix: str = "",
                   note: str = "") -> Path:
    """Write an evalharness manifest for the generated audio.

    ``audio_of`` maps utterance id → generated wav path (paths are made
    relative to the manifest's directory). Negative cases carry empty
    references (every word is a hallucination, G3) and expected_guard
    "flag"; speech cases mirror the daemon's provenance via
    speaker/mic_class labels.
    """
    lines = [
        f"# Generated by fluidvoice.evalharness.tts_bench at "
        f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"# {note}" if note else "# TTS decode-baseline run.",
        "",
    ]

    def rel_audio(audio: Path) -> str:
        """Audio path relative to the manifest's directory."""
        return os.path.relpath(audio, path.parent)

    for u in speech:
        audio = audio_of[u.id]
        lines += [
            "[[cases]]",
            f"id = {_toml_str(u.id + suffix)}",
            f"audio = {_toml_str(rel_audio(audio))}",
            f"reference_text = {_toml_str(u.text)}",
            'language = "en"',
            f"tags = [{', '.join(_toml_str(t) for t in u.tags)}]",
            'expected_guard = "ok"',
            f"license = {_toml_str(_LICENSE_LABEL)}",
            f"source = {_toml_str(_SOURCE_LABEL)}",
            f"speaker = {_toml_str(_SPEAKER_LABEL)}",
            'mic_class = "tts-synthetic"',
            f"taxonomy = {_toml_str(u.taxonomy)}",
            "",
        ]
    for neg, audio in negatives:
        lines += [
            "[[cases]]",
            f"id = {_toml_str(neg.id + suffix)}",
            f"audio = {_toml_str(rel_audio(audio))}",
            'reference_text = ""',
            'language = "en"',
            "tags = []",
            'expected_guard = "flag"',
            f"license = {_toml_str(_LICENSE_LABEL)}",
            f"source = {_toml_str(_SOURCE_LABEL)}",
            f"speaker = {_toml_str(_SPEAKER_LABEL)}",
            'mic_class = "tts-synthetic"',
            f'taxonomy = {_toml_str("negative-" + neg.kind)}',
            "",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ── run orchestration ──────────────────────────────────────────────────────

def _loadavg() -> str:
    try:
        return Path("/proc/loadavg").read_text().strip()
    except OSError:
        return "unknown"


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def _collect_env(voice: Path, piper: Path | None) -> dict[str, Any]:
    return {
        "backend": probe_backend_env(),
        "cpu_model": _cpu_model(),
        "voice": str(voice),
        "piper": str(piper) if piper else None,
        "loadavg_start": _loadavg(),
        "generated_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
    }


def _stamp_env(report: dict[str, Any], env: dict[str, Any],
               out_dir: Path, extra: dict[str, Any] | None = None) -> None:
    """Attach run context to a run_eval report dict (unknown top-level
    keys are tolerated by the report renderer) and rewrite its files
    in ``out_dir``."""
    from .report import render_json, render_markdown
    report["tts_bench_env"] = {**env, **(extra or {})}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(render_json(report) + "\n",
                                       encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(report) + "\n",
                                     encoding="utf-8")


def summarize(report: dict[str, Any]) -> str:
    """Markdown fragment for one run_eval report dict (pure — unit
    tested against a fake dict, never needs a model)."""
    lines: list[str] = []
    s = report.get("summary", {})
    lines.append("| cases | err | WER | CER | omiss | hotw | RTF "
                 "(>1=rt) | final s |")
    lines.append("|---|---|---|---|---|---|---|---|")

    def n(v: Any) -> str:
        return "n/a" if v is None else f"{v:.3f}"

    lines.append(
        f"| {s.get('cases', 0)} | {s.get('errors', 0)} "
        f"| {n(s.get('wer_mean'))} | {n(s.get('cer_mean'))} "
        f"| {n(s.get('omission_mean'))} "
        f"| {n(s.get('hotword_recall_mean'))} "
        f"| {n(s.get('real_time_factor_mean'))} "
        f"| {n(s.get('final_latency_mean_s'))} |")
    tax_rows = [r for r in report.get("subgroups", [])
                if r.get("dimension") == "taxonomy"]
    if tax_rows:
        lines.append("")
        lines.append("| taxonomy | cases | WER | CER | omiss | hotw "
                     "| RTF | final s |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in tax_rows:
            lines.append(
                f"| {r['group']} | {r.get('cases', 0)} "
                f"| {n(r.get('wer_mean'))} | {n(r.get('cer_mean'))} "
                f"| {n(r.get('omission_mean'))} "
                f"| {n(r.get('hotword_recall_mean'))} "
                f"| {n(r.get('real_time_factor_mean'))} "
                f"| {n(r.get('final_latency_mean_s'))} |")
    neg = report.get("negatives", {})
    if neg.get("cases"):
        lines.append("")
        lines.append("| negative | tokens | audio s | words/s |")
        lines.append("|---|---|---|---|")
        for r in neg["cases"]:
            lines.append(
                f"| {r['id']} | {r['tokens']} "
                f"| {n(r.get('audio_duration_s'))} "
                f"| {n(r.get('hallucination_word_rate'))} |")
    conf = report.get("language_confusion", {})
    if conf.get("rows"):
        lines.append("")
        lines.append("| expected | detected | match | cases |")
        lines.append("|---|---|---|---|")
        for r in conf["rows"]:
            lines.append(f"| {r['expected']} | {r['detected']} "
                         f"| {'yes' if r['match'] else 'NO'} "
                         f"| {r['cases']} |")
    return "\n".join(lines)


TranscriberFactory = Callable[[], Transcriber]


def run_bench(workdir: Path = DEFAULT_WORKDIR, *,
              piper: Path | None = None,
              voice: Path = DEFAULT_VOICE,
              limit: int | None = None,
              use_ffmpeg: bool = True,
              auto: bool = True,
              transcriber_factory: TranscriberFactory = make_transcriber,
              auto_transcriber_factory: TranscriberFactory =
                  make_auto_transcriber,
              ) -> dict[str, Any]:
    """Generate audio, run both passes, write reports under ``workdir``.

    Returns {"forced": report, "auto": report | None, "env": …,
    "generation_failures": [(id, error)], "runtime_cut": str | None}.
    Audio stays under ``workdir`` — outside git by construction.
    """
    workdir = Path(workdir)
    audio_dir = workdir / "audio"
    workdir.mkdir(parents=True, exist_ok=True)
    piper = piper or piper_binary()
    if piper is None:
        raise RuntimeError(
            "no runnable piper found — install one into the bench venv "
            f"({BENCH_VENV_PIPER.parent.parent}) or pass --piper PATH")
    env = _collect_env(voice, piper)
    print(f"[tts-bench] backend: "
          f"{env['backend']['resolved_backend']} "
          f"({env['backend']['resolved_model']}), "
          f"cpu: {env['cpu_model']}, load {env['loadavg_start']}",
          flush=True)

    utts, negs = load_reference()
    runtime_cut = None
    if limit is not None and limit < len(utts):
        limit = max(MIN_SPEECH_CASES, limit)
        runtime_cut = (f"speech cases cut {len(utts)} -> {limit} "
                       "(runtime guard)")
        utts = utts[:limit]
        print(f"[tts-bench] {runtime_cut}", flush=True)

    # 1. TTS generation — failures are recorded and skipped, not fatal.
    failures: list[tuple[str, str]] = []
    audio_of: dict[str, Path] = {}
    durations: dict[str, float] = {}
    methods: dict[str, str] = {}
    total_speech_s = 0.0
    for u in utts:
        out = audio_dir / f"{u.id}.wav"
        try:
            path, duration, method = synth_utterance(
                piper, voice, u.text, out, use_ffmpeg=use_ffmpeg)
            audio_of[u.id] = path
            durations[u.id] = duration
            methods[u.id] = method
            total_speech_s += duration
            print(f"[tts-bench] {u.id}: {duration:.1f}s [{method}]",
                  flush=True)
        except Exception as e:  # noqa: BLE001 - record, don't die
            failures.append((u.id, f"{type(e).__name__}: {e}"))
            print(f"[tts-bench] {u.id}: FAILED {e}", flush=True)
    kept = [u for u in utts if u.id in audio_of]
    if len(kept) < MIN_SPEECH_CASES:
        raise RuntimeError(
            f"TTS generation produced only {len(kept)}/{len(utts)} clips "
            f"— below the {MIN_SPEECH_CASES}-case floor; not a "
            "representative run")
    if failures:
        print(f"[tts-bench] {len(failures)} generation failures "
              f"(recorded, cases skipped)", flush=True)

    # 2. Negatives.
    neg_paths = generate_negatives(negs, audio_dir)
    neg_audio_s = sum(n.seconds for n, _ in neg_paths)
    print(f"[tts-bench] negatives: {len(neg_paths)} "
          f"({neg_audio_s:.0f}s audio)", flush=True)

    # 3. Forced pass (production mirror): every kept speech case.
    forced_manifest = write_manifest(
        workdir / "forced-manifest.toml", kept, neg_paths, audio_of,
        note="forced-en production mirror")
    forced_cases = load_manifest(forced_manifest)
    t0 = time.perf_counter()
    report_forced = run_eval(
        forced_cases, transcriber_factory(),
        out_dir=workdir / "report-forced",
        manifests=[forced_manifest])
    forced_wall = time.perf_counter() - t0
    print(f"[tts-bench] forced pass: {forced_wall:.0f}s wall",
          flush=True)
    _stamp_env(report_forced, env, workdir / "report-forced", {
        "pass": "forced (production mirror, language=en)",
        "wall_s": round(forced_wall, 1),
        "speech_audio_s": round(total_speech_s, 1),
        "negative_audio_s": round(neg_audio_s, 1),
        "conversion": (f"voice→{TARGET_RATE}Hz: "
                       f"{sorted(set(methods.values()))}"),
        "generation_failures": failures or None,
        "runtime_cut": runtime_cut,
        "loadavg_end": _loadavg()})

    # 4. Auto probe pass (subset + negatives, language auto).
    report_auto = None
    if auto:
        probe = {u.id for u in kept
                 if u.id in set(auto_probe_ids(kept))}
        auto_utts = [u for u in kept if u.id in probe]
        auto_manifest = write_manifest(
            workdir / "auto-manifest.toml", auto_utts, neg_paths,
            audio_of, suffix="-auto",
            note="auto-language probe (G5 detection + G3 auto negatives)")
        auto_cases = load_manifest(auto_manifest)
        t0 = time.perf_counter()
        report_auto = run_eval(
            auto_cases, auto_transcriber_factory(),
            out_dir=workdir / "report-auto",
            manifests=[auto_manifest])
        auto_wall = time.perf_counter() - t0
        print(f"[tts-bench] auto pass: {auto_wall:.0f}s wall", flush=True)
        _stamp_env(report_auto, env, workdir / "report-auto", {
            "pass": "auto probe (language=auto)",
            "wall_s": round(auto_wall, 1)})

    summary = ["# TTS decode-bench summary", "",
               f"Generated {env['generated_utc']} UTC.", "",
               "## Forced pass (production mirror)", "",
               summarize(report_forced)]
    if report_auto is not None:
        summary += ["", "## Auto probe", "", summarize(report_auto)]
    if failures:
        summary += ["", "## Generation failures", ""]
        summary += [f"- {cid}: {err}" for cid, err in failures]
    if runtime_cut:
        summary += ["", f"Runtime cut: {runtime_cut}."]
    (workdir / "summary.md").write_text("\n".join(summary) + "\n",
                                        encoding="utf-8")
    print(f"[tts-bench] done -> {workdir / 'summary.md'}", flush=True)
    return {"forced": report_forced, "auto": report_auto, "env": env,
            "generation_failures": failures, "runtime_cut": runtime_cut}


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m fluidvoice.evalharness.tts_bench",
        description="TTS decode baseline: piper-rendered reference set "
                    "+ negatives through the production speech backend "
                    "(see docs/eval/decode-baseline-2026-09-11.md)")
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR,
                        help=f"audio + reports workdir, outside git "
                        f"(default: {DEFAULT_WORKDIR})")
    parser.add_argument("--piper", type=Path, default=None,
                        help="piper executable (default: bench venv, "
                        "then PATH)")
    parser.add_argument("--voice", type=Path, default=DEFAULT_VOICE,
                        help="piper onnx voice (default: "
                        "en_US-lessac-medium)")
    parser.add_argument("--limit", type=int, default=None,
                        metavar="N",
                        help="runtime guard: cut speech cases to N "
                        "(minimum 15); the cut is recorded")
    parser.add_argument("--no-auto", action="store_true",
                        help="skip the auto-language probe pass")
    parser.add_argument("--no-ffmpeg", action="store_true",
                        help="force the pure-python sinc resampler")
    args = parser.parse_args(argv)
    if not args.voice.is_file():
        print(f"error: piper voice not found: {args.voice}", flush=True)
        return 1
    result = run_bench(workdir=args.workdir, piper=args.piper,
                       voice=args.voice, limit=args.limit,
                       use_ffmpeg=not args.no_ffmpeg,
                       auto=not args.no_auto)
    nf = len(result["generation_failures"])
    print(f"generation failures: {nf}; runtime cut: "
          f"{result['runtime_cut'] or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
