# Plan: `fluidvoice transcribe` v1 — verified formats + ffmpeg fallback + `--json`/`--out`

Session `eaed8a8c` · baseline HEAD `412617f` · suite `.venv/bin/python -m pytest -q tests --ignore=tests/integration` = **413 passed** (~28 s).

Scope: UPSTREAM-TRACKING row "File transcription: chunked API uploads, `.opus`/`.oga` input" (formats + structured output only — **no chunking**) and the speaker-labeling prerequisite "timestamps/JSON export" (delivered here as segment timestamps).

Out of scope (do NOT build): chunked API uploads, diarization/speaker labels, streaming, remote URLs, GPU/model changes.

---

## 1. Format verification findings (measured on this machine)

Method: 0.5 s 16 kHz sine generated with system `ffmpeg` 6.1.1-3ubuntu5, then decoded with `faster_whisper.audio.decode_audio` (PyAV 18.1.0 bundled libs, faster-whisper 1.2.1).

**All 12 extensions decode directly via PyAV at 16 kHz mono:**

| ext | PyAV decode | ext | PyAV decode |
|---|---|---|---|
| `.wav` | ✅ | `.m4a` | ✅ |
| `.flac` | ✅ | `.aac` | ✅ |
| `.mp3` | ✅ | `.wma` | ✅ |
| `.opus` | ✅ | `.aiff` / `.aif` | ✅ |
| `.oga` / `.ogg` | ✅ | `.webm` | ✅ |

Notes:
- `decode_audio` in this faster-whisper has signature `decode_audio(input_file, sampling_rate=16000)` (no `mono` kwarg); mono mixing is internal.
- faster-whisper and openai-whisper (torch) decode these directly when the file is passed through. **whisper.cpp's `whisper-cli` only reliably reads WAV** — non-wav must be converted for that backend.
- CI (`.github/workflows/ci.yml`) does **not** install the `ffmpeg` binary — unit tests must mock it; only a `skipif`-guarded test may use the real binary.
- Docs/README/help must state only this verified set.

---

## 2. Design decisions

### 2.1 `ensure_wav` (fluidvoice/audio_utils.py)

New public surface (append to the existing module; add `shutil`, `subprocess`, `tempfile`, `from pathlib import Path` to imports):

```python
SUPPORTED_AUDIO_EXTS = frozenset({
    ".wav", ".flac", ".mp3", ".opus", ".oga", ".ogg",
    ".m4a", ".aac", ".wma", ".aiff", ".aif", ".webm",
})  # verified via PyAV 18.1.0 / ffmpeg 6.1.1 (specs/eaed8a8c_transcribe-formats-json.md)

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
    """Return a decodable audio path; convert to 16 kHz mono WAV only when needed."""
```

Decision matrix:

| input | `force=False` (CLI default) | `force=True` (whisper.cpp) |
|---|---|---|
| `.wav` | passthrough, no subprocess | ffmpeg re-encode if ffmpeg exists, else passthrough |
| other, PyAV-decodable | passthrough | ffmpeg convert |
| other, not decodable | ffmpeg convert | ffmpeg convert |
| conversion needed, no ffmpeg | `AudioFormatError` naming ffmpeg | same |

Conversion mechanics:
- Command: `[ffmpeg, "-hide_banner", "-v", "error", "-i", str(path), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y", str(out)]` via `subprocess.run(..., capture_output=True, text=True, timeout=1800)`; ffmpeg resolved with `shutil.which("ffmpeg")`.
- Nonzero exit → `AudioFormatError(f"ffmpeg failed to convert {path.name}: {proc.stderr.strip()[:300]}")`.
- Output file: `dest_dir / f"{path.stem}.16k.wav"`; `dest_dir` defaults to `Path(tempfile.mkdtemp(prefix="fluidvoice-wav-"))`. **Caller owns cleanup** of the returned temp dir when the returned path differs from the input.
- Missing-ffmpeg message (must contain the word `ffmpeg` and a fix): `cannot decode '<name>' (<suffix>): format not directly decodable and ffmpeg is not installed - install ffmpeg (e.g. `sudo apt install ffmpeg`) or convert to 16 kHz mono WAV yourself`.

The CLI passes `force=(backend.name == "whisper.cpp")` so the whisper.cpp backend file needs **no format-related change**. The daemon is untouched (it always feeds recorder-made 16 kHz WAV).

### 2.2 Segment exposure (backends)

All `transcribe()` dicts gain a `"segments"` key (additive — daemon/preview only read `text`):

- **faster_whisper_backend.py** (minimal change; the segment generator is consumed exactly once, building text and segments in the same loop):

```python
texts, segs = [], []
for seg in segments:  # generator - consume once, reuse for text AND segments
    texts.append(seg.text)
    segs.append({"start": round(seg.start, 3), "end": round(seg.end, 3),
                 "text": seg.text.strip()})
return {"text": "".join(texts).strip(), "language": info.language,
        "duration": info.duration, "segments": segs}
```

- **torch_whisper.py**: openai-whisper already returns `result["segments"]` as dicts — expose them (2 lines): `[{"start": round(s.get("start", 0.0), 3), "end": round(s.get("end", 0.0), 3), "text": (s.get("text") or "").strip()} for s in result.get("segments", [])]`.
- **whisper_cpp.py**: add `"segments": []` with comment `# segments not exposed in v1: needs whisper-cli -ml parsing`.

### 2.3 CLI (`fluidvoice transcribe`)

New args on the transcribe parser (alongside `--no-process/--ai/--config`):
- `--json` — `help="print structured JSON {text, language, duration_s, segments} instead of plain text"`
- `--out PATH` — `help="write the result to PATH instead of stdout (plain text, or JSON with --json)")`
- Update `file` help to `audio file (wav/flac/mp3/opus/oga/ogg/m4a/aac/wma/aiff/webm)`.

Revised transcribe branch (single transcription call — never transcribe twice):

```python
if args.cmd == "transcribe":
    from . import backends
    from .ai.client import AIClient
    from .audio_utils import SUPPORTED_AUDIO_EXTS, AudioFormatError, ensure_wav
    from .processing import post_process
    if not args.file.exists():
        print(f"error: file not found: {args.file}", file=sys.stderr)
        return 1
    if args.file.stat().st_size > LARGE_INPUT_BYTES:   # 25 * 1024 * 1024, cli.py constant
        print(f"warning: input is {args.file.stat().st_size / 1e6:.1f} MB; transcription "
              f"is not chunked in v1 and may be slow/memory-heavy. Shrinking first usually "
              f"helps: ffmpeg -i {args.file} -ar 16000 -ac 1 out.wav", file=sys.stderr)
    cfg = load_config(args.config)
    backend = backends.load_backend(cfg)
    if args.file.suffix.lower() not in SUPPORTED_AUDIO_EXTS:
        print(f"note: '{args.file.suffix}' is not a verified format - trying anyway "
              f"(ffmpeg fallback when needed)", file=sys.stderr)
    audio, converted_dir = args.file, None
    try:
        try:
            audio = ensure_wav(args.file, force=backend.name == "whisper.cpp")
        except AudioFormatError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        if audio != args.file:
            converted_dir = audio.parent
        result = backend.transcribe(audio, language=cfg["general"]["language"])
    finally:
        if converted_dir is not None:
            shutil.rmtree(converted_dir, ignore_errors=True)
    text = result["text"]
    if not args.no_process:
        text = post_process(text, cfg)
    if args.ai and cfg["ai"].get("enabled"):
        text = AIClient(cfg).polish(text)
    elif args.ai:
        print("(ai.enabled=false in config; raw transcription only)", file=sys.stderr)
    if args.json:
        payload = {"text": text,                       # final text (post-processed/AI if on)
                   "language": result.get("language"),
                   "duration_s": result.get("duration"),   # null for torch/whisper.cpp
                   "segments": result.get("segments", [])}  # raw per-segment text, not post-processed
        out_text = json.dumps(payload, indent=2, ensure_ascii=False)
    else:
        out_text = text
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(out_text + "\n", encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)    # stderr keeps stdout script-clean
    else:
        print(out_text)
    return 0
```

Add `import shutil` to cli.py's top imports and `LARGE_INPUT_BYTES = 25 * 1024 * 1024` near the top of the module. `result.get(...)` everywhere so stub/older backends without the new keys still work.

---

## 3. Phases (each ends with the full suite green)

### Phase 1 — `ensure_wav` + unit tests
**Change:** `fluidvoice/audio_utils.py` per §2.1 (nothing else calls it yet).
**New `tests/test_audio_utils.py`** (pure unit; no real ffmpeg/av needed — monkeypatch `fluidvoice.audio_utils._pyav_decodable`, `shutil.which`, `subprocess.run`):
1. `test_wav_passthrough_no_subprocess` — `.wav` returns same path; patched `subprocess.run` not called.
2. `test_pyav_decodable_passthrough` — non-wav with `_pyav_decodable→True` returns same path, no subprocess.
3. `test_ffmpeg_conversion_command` — `_pyav_decodable→False`, `which→"/usr/bin/ffmpeg"`; fake `run` asserts flags `-ar 16000 -ac 1 pcm_s16le -y` and the input path in argv; returned path ends `.wav` and differs from input.
4. `test_ffmpeg_failure_raises` — fake `run` returncode 1, stderr `"boom"` → `AudioFormatError` containing `boom`.
5. `test_missing_ffmpeg_error_names_ffmpeg` — `_pyav_decodable→False`, `which→None` → `AudioFormatError`, `"ffmpeg" in str(e)`.
6. `test_force_wav_reencodes` / `test_force_wav_without_ffmpeg_passthrough`.
7. `test_real_ffmpeg_flac_roundtrip` — `@pytest.mark.skipif(not shutil.which("ffmpeg"), ...)`; build wav via `tests.test_daemon.make_wav`, encode to `.flac` with real ffmpeg, `ensure_wav(flac, force=True)` → open with `wave`: 16000 Hz, mono.
Reuse `make_wav` from `tests/test_daemon.py` (precedent: `test_extra_formats.py` imports it).

### Phase 2 — segment exposure
**Change:** three backends per §2.2. Nothing consumes `"segments"` yet, so this is additive and safe.
**New `tests/test_backend_segments.py`** — bypass constructors with `object.__new__`, set `_model`/attrs to fakes:
- faster-whisper: `_model.transcribe` returns an iterator of fake seg objects (`.start/.end/.text`, leading-space text like `" And so"`) + fake `info` → assert text join/strip and rounded segment dicts; assert generator consumed once (len(calls)).
- torch: fake `result = {"text": " hi ", "language": "en", "segments": [{"start": 0.2, "end": 1.0, "text": " hi "}]}` → segments stripped/rounded.
- whisper.cpp: monkeypatch `subprocess.run` in `whisper_cpp` module, set `binary`/`model` attrs → returns `"segments": []`.

### Phase 3 — CLI `--json` / `--out` / formats / warning
**Change:** `fluidvoice/cli.py` transcribe parser + branch per §2.3 (help text included here).
**New `tests/test_transcribe_cli.py`** — `monkeypatch.setattr(backends, "load_backend", lambda cfg: stub)` and `monkeypatch.setattr(cli, "load_config", lambda p=None: copy.deepcopy(DEFAULTS))`; stub subclasses `tests.test_daemon.StubBackend` returning `{"text", "language", "duration", "segments"}`; wav via `make_wav`; `capsys` + tmp_path:
1. plain default unchanged: stdout == `post_process(text, cfg)` output; and with `--no-process`, stdout == raw text.
2. `--json --no-process`: `json.loads(stdout)` == `{"text": …, "language": "en", "duration_s": …, "segments": [{start, end, text}]}` (exact keys).
3. `--json` with stub returning **no** `segments` key → `"segments" == []`.
4. `--out` plain: file contains text + newline; stdout empty.
5. `--out --json`: file parses as JSON; confirmation goes to stderr only.
6. `--out` into missing nested dirs → parent created.
7. transcribe called exactly once under `--json` (long-file sanity; count `stub.calls`).
8. >25 MB: `open(p,"wb").truncate(LARGE_INPUT_BYTES + 1)` sparse file → stderr matches `warning` and `ffmpeg`; exit 0, still transcribed.
9. missing file → rc 1, stderr `not found`.
10. unlisted extension (`x.amr` with a few bytes) → stderr note `not a verified format`, rc 0 (probe decides passthrough vs ffmpeg).

### Phase 4 — docs & doctor
- `README.md` "Useful commands" block: change the transcribe line to `fluidvoice transcribe x.opus --json`, and add a short **File transcription** paragraph right after the block: verified format list, ffmpeg fallback behavior, `--json` schema (`segments` are raw per-segment text; `[]` on whisper.cpp), `--out`, and the >25 MB no-chunking warning with the ffmpeg shrink command.
- `docs/UPSTREAM-TRACKING.md`:
  - line 94 row → note: "`transcribe` accepts opus/oga + 10 more verified formats with ffmpeg fallback; `--json` exports timestamps/segments; **chunked API uploads still pending**" (row stays ⏳).
  - line 79 (speaker labeling) note → "not started; timestamps/JSON export shipped via `transcribe --json`; diarization pending".
- `docs/STATUS.md` Infrastructure CLI bullet (~line 93): extend to `transcribe (multi-format + --json/--out)`.
- `fluidvoice/doctor.py` tools list: add `("ffmpeg", "transcribe fallback for non-WAV/undecodable input")` — display-only, never flips `ok` to False.

### Phase 5 — final verification
1. `.venv/bin/python -m pytest -q tests --ignore=tests/integration` → green (413 + new ≈ 25 tests).
2. Grep gates: `grep -n "wav/flac/mp3" fluidvoice/cli.py` → empty (help updated); docs mention only verified formats.
3. Manual smoke (model cache already on this machine; ffmpeg present):
   - `ffmpeg -v error -f lavfi -i "sine=frequency=440:duration=0.5" -y /tmp/smoke.opus`
   - `.venv/bin/python -m fluidvoice transcribe /tmp/smoke.opus --no-process --json` → JSON with `duration_s` and `segments` on stdout.
   - `.venv/bin/python -m fluidvoice transcribe /tmp/smoke.opus --out /tmp/smoke.txt` → file written, `wrote /tmp/smoke.txt` on stderr.
   - `.venv/bin/fluidvoice transcribe --help` → shows formats + new flags.

---

## 4. Risks / notes for the builder
- `subprocess.run` mocking: patch via `monkeypatch.setattr("fluidvoice.audio_utils.subprocess.run", fake)` — it reverts after each test.
- `cli.py` imports `load_config` at module top (`from .config import load_config`) — patch `fluidvoice.cli.load_config`, not `fluidvoice.config.load_config`; `backends` is imported inside the branch — patch the `fluidvoice.backends.load_backend` module attribute (works either way).
- Keep `result.get("segments", [])` in the CLI — daemon stubs and old backends return no such key.
- Do not re-call `backend.transcribe` for JSON — one call feeds text, language, duration, segments.
- Unknown extensions are attempted (probe + ffmpeg fallback), not rejected — docs list only verified formats.
- Commit one phase at a time; suite green at each stop.
