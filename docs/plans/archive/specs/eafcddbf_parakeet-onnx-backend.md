# Plan: Parakeet TDT backend via ONNX Runtime (ROADMAP v0.4)

Session `eafcddbf` · verified at repo HEAD `c318daa` (suite 527 green per task brief)
· companion artifact: `adws/adw_data/sessions/eafcddbf/context_handoff/parakeet_reference_decode.py`
(a runnable, **already executed against the real models** reference decode — see §2).

**Goal.** `model.backend = "parakeet"` gives Linux a one-click, auto-downloaded
NVIDIA Parakeet TDT v2 (EN) / v3 (multilingual) engine: ONNX Runtime inference,
pure-numpy featurization + TDT greedy decode, fully offline after download.
Explicit selection only — the "auto" priority order is **not** touched
(deliberate divergence from upstream, noted in docs/STATUS.md).

---

## 1. Verified ground truth (do not re-derive; everything below was executed)

### 1.1 Model sources — real, resolvable, checksummed

Chosen source: the **sherpa-onnx community exports** of `nvidia/parakeet-tdt-0.6b-v2/v3`
(k2-fsa GitHub release, `asr-models` tag). The HF candidates (`istupane/...`,
`csukuangfj/...`) could not be verified from this environment (HF egress is
auth-walled); the GitHub tarballs were downloaded and hashed today:

| catalog key | tarball | size | sha256 |
|---|---|---|---|
| `parakeet-tdt-0.6b-v2` | `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2` | 482,468,385 B | `157c157bc51155e03e37d2466522a3a737dd9c72bb25f36eb18912964161e1ad` |
| `parakeet-tdt-0.6b-v3` | `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2` | 487,170,055 B | `5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf` |

Tarball layout (top dir `sherpa-onnx-nemo-parakeet-tdt-0.6b-vN-int8/`):
`encoder.int8.onnx`, `decoder.int8.onnx`, `joiner.int8.onnx`, `tokens.txt`
(+ `test_wavs/`, not extracted). Per-file sha256 (post-extract, committed to the catalog):

| file | v2 sha256 | v2 bytes | v3 sha256 | v3 bytes |
|---|---|---|---|---|
| `encoder.int8.onnx` | `a32b12d17bbbc309d0686fbbcc2987b5e9b8333a7da83fa6b089f0a2acd651ab` | 652,184,296 | `acfc2b4456377e15d04f0243af540b7fe7c992f8d898d751cf134c3a55fd2247` | 652,184,281 |
| `decoder.int8.onnx` | `b6bb64963457237b900e496ee9994b59294526439fbcc1fecf705b31a15c6b4e` | 7,257,753 | `179e50c43d1a9de79c8a24149a2f9bac6eb5981823f2a2ed88d655b24248db4e` | 11,845,275 |
| `joiner.int8.onnx` | `7946164367946e7f9f29a122407c3252b680dbae9a51343eb2488d057c3c43d2` | 1,739,080 | `3164c13fc2821009440d20fcb5fdc78bff28b4db2f8d0f0b329101719c0948b3` | 6,355,277 |
| `tokens.txt` | `ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d` | 9,384 | `d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d` | 93,939 |

On-disk footprint ≈ 630 MB (v2) / 640 MB (v3) after the tarball is deleted.
Languages: v2 = English (with punctuation + true case); v3 = 25 European
languages + ru/uk, auto-detected (no language input on these graphs).
Tokens format: `<piece> <id>` per line; first line `<unk> 0` (v2) / `<unk> 0`,
`<|nospeech|> 1`, `<pad> 2` (v3); **last line is the blank** (`<blk> 1024` v2,
`<blk> 8192` v3) → `blank = len(vocab) - 1`.

### 1.2 Graph contract (identical for v2/v3; bind I/O **positionally**)

```
encoder  in : audio_signal [1,128,T] f32 , length [1] i64
         out: outputs [1,1024,T'] f32    , encoded_lengths [1] i64
         custom_metadata_map: feat_dim=128, vocab_size, pred_hidden=640,
                              pred_rnn_layers=2, normalize_type=per_feature,
                              subsampling_factor=8, model_type=EncDecRNNTBPEModel
decoder  in : targets [1,1] i32, target_length [1] i32,
              state0/st1 [pred_rnn_layers,1,pred_hidden] f32   (zeros at start)
         out: outputs [1,640,1] f32, prednet_lengths, state0_next, state1_next
joiner   in : encoder_outputs [1,1024,1] f32, decoder_outputs [1,640,1] f32
         out: outputs [1,1,1,V+D] f32   (V = vocab incl. blank; D = TDT duration
              bins: 6 for v2)  → token_logits = logits[:V], duration_logits = logits[V:]
```

Names vary across exports (e.g. decoder state input literally `onnx::Slice_3`);
use `session.get_inputs()[i].name` / `get_outputs()[i].name` like the reference
script does. Assert input/output counts (2 / 4 / 2) at load time to fail fast.

### 1.3 Feature spec (verified: this exact numpy recipe reproduces the
reference transcripts bit-for-bit on both models)

16 kHz mono float32 samples (`int16/32768`), **append 2.0 s of zeros**, then:
- framing: window 400 samples (25 ms), hop 160 (10 ms), **no centering**;
  `T = 1 + (N - 400)//160` (0 frames when N < 400)
- window: **periodic** hann `0.5 - 0.5*cos(2πn/400)`, n = 0..399; frame
  zero-padded to `n_fft = 512`, `np.fft.rfft` → power = re²+im² (257 bins)
- mel: **librosa/slaney** filterbank, `n_mels = 128`, `fmin = 0`, `fmax = 8000`,
  htk=False, norm='slaney' (formulas in §3.1; ~30 lines numpy)
- log: natural log with floor → `np.log(np.maximum(mel_power, 1e-10))`
- normalize (encoder metadata says `per_feature`): per-channel over time —
  `(f - mean_t) / (std_t + 1e-5)`
- encoder input layout: **transposed** `(T, 128) → [1, 128, T]`

### 1.4 Greedy TDT decode loop (verified)

```
enc_out, _ = encoder(...)                        # [1,1024,T']
blank = len(id2tok) - 1
state0 = state1 = zeros(pred_rnn_layers, 1, pred_hidden)
dec_out, _, s0n, s1n = decoder([[blank]], states)
t, emitted = 0, []
while t < T':
    logits = joiner(enc_out[:,:,t:t+1], dec_out).squeeze()   # V+D
    idx  = argmax(logits[:V])                                 # V = blank+1
    skip = max(argmax(logits[V:]), 1)                         # duration 0 → 1
    if idx != blank:
        emitted.append(idx); state0, state1 = s0n, s1n
        dec_out, _, s0n, s1n = decoder([[idx]], state0, state1)
    t += skip
    # guard: len(emitted) < max_tokens (4*T' + 16) else break
tokens = emitted
text = "".join(id2tok[i]).replace("▁", " ")  → collapse runs of spaces, strip
```

### 1.5 Golden numbers & transcripts (pin these in tests)

- 1600-sample (0.1 s) 0.5-amplitude 1 kHz sine → featurizer output shape `(8, 128)`
- its frame-0 power spectrum peaks at FFT bin **32** with value **2500.0**
  (= (A/2·Σhann)² = (0.25·200)²; measured 2499.9991; >99.9 % energy in bins 30–34)
- `logmel[0, :3] == [-16.85122337, -16.30044520, -15.92483247]` (tol 1e-4)
- all-zero input → every bin exactly `log(1e-10) = -23.02585093`
- 1 kHz tone lands in mel row **42** of frame 0
- v2 transcripts `test_wavs/0.wav` (7.435 s):
  `"Well, I don't wish it any more, observed Phebe, turning away her eyes. It is certainly very like the old portrait."`
- v3 `en.wav` (24 kHz stereo upstream; 16 k mono s16 after conversion):
  `"Ask not what your country can do for you. Ask what you can do for your country."`
- v3 `fr.wav`: `"Ne vous demandez pas ce que votre pays peut faire pour vous, demandez-vous plutôt ce que vous pouvez faire pour lui."`
- perf sanity (this box, CPU EP, int8, cold session): 7.4 s audio end-to-end ≈ 2.5 s wall.

### 1.6 Existing machinery to reuse

- `fluidvoice/model_download.py`: `download_file(url, dest, progress)` — streaming,
  sibling `.part`, atomic `os.replace`, truncation check. Reuse verbatim for the tarball.
- `fluidvoice/model_catalog.py` + `paths.models_dir()` cache layout.
- `backends/__init__.py`: `_import_ok`, lazy per-branch imports in `load_backend`.
- `transcribe()` result contract (see `faster_whisper_backend.py:46-52`, consumed by
  `daemon.py:71` + `cli.py:149`): `{"text": str, "language": str|None,
  "duration": float|None, "segments": [{"start","end","text"}]}`.
- GTK GGUF group pattern (`settings_window.py:385-529` + tests at
  `tests/test_gtkui.py:385-486`), doctor pattern (`doctor._whispercpp_lines` +
  `tests/test_infra.py:35-39`), download-test fakes (`tests/test_model_download.py`).
- Daemon records **16 kHz mono s16 WAV** (`recorder.py:3,58`) — stdlib `wave` reads it.

### 1.7 Hard constraints

- repo `.venv` has pydantic 1.x and **no onnxruntime**: tests must never import
  onnxruntime at collection time. `parakeet_onnx.py` module-level imports =
  stdlib + numpy + `..model_catalog` only; `import onnxruntime` lives inside
  `_load()`. `backend_status`/doctor import it inside functions.
- Unit tests: **no network, no real ONNX models** (stub sessions, tiny tar.bz2
  fixtures, monkeypatched catalog where checksums are needed).
- Suite must stay green after every phase:
  `.venv/bin/python -m pytest -q tests --ignore=tests/integration`
- Do NOT change the auto backend priority; do NOT add NeMo/torchaudio/librosa deps.

---

## 2. Phases

### Phase 1 — catalog + multi-file download machinery

**`fluidvoice/model_catalog.py`** (append after GGUF section):

```python
PARAKEET_TARBALL_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
PARAKEET_CATALOG: dict[str, dict] = {
    "parakeet-tdt-0.6b-v2": {
        "size": "~630 MB", "langs": "en",
        "note": "NVIDIA Parakeet TDT 0.6B v2 — English, punctuation + true case",
        "url": PARAKEET_TARBALL_BASE + "/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2",
        "tarball_sha256": "157c...e1ad",          # full values from §1.1
        "files": { "encoder.int8.onnx": "a32b...51ab", "decoder.int8.onnx": "b6bb...c6b4e",
                   "joiner.int8.onnx":  "7946...43d2", "tokens.txt": "ec18...497d" },
        "features": {"sample_rate": 16000, "n_mels": 128, "n_fft": 512,
                     "win": 400, "hop": 160, "fmin": 0.0, "fmax": 8000.0},
    },
    "parakeet-tdt-0.6b-v3": { ...same shape, langs "25 EU + ru/uk",
        "note": "NVIDIA Parakeet TDT 0.6B v3 — multilingual (25 European languages)" ... },
}
PARAKEET_DIR_NAME = "parakeet"
PARAKEET_DEFAULT_MODEL = "parakeet-tdt-0.6b-v2"   # explicit-backend default; upstream defaults to v3 — divergence noted in STATUS.md

def parakeet_dir() -> Path: return paths.models_dir() / PARAKEET_DIR_NAME
def parakeet_model_dir(name: str) -> Path: return parakeet_dir() / name
def parakeet_downloaded(name: str) -> bool:
    """True when every catalog-listed file exists in parakeet/<name>/."""
```

Design note (documented deviation from the task letter): the task suggested
per-file `{name,url,size}` lists. The verified community source ships **one
tarball per model**; per-file HTTP sources for these exact files are only
available via unverified HF mirrors. So: catalog `url`+`tarball_sha256` is the
download source, and `files` (name → sha256) is the **post-extract integrity +
presence manifest** — checksums are still committed per file, verified after
extraction, exactly the "corrupt silent model" protection asked for. The
generic multi-file downloader below also lands now (tested with fake per-file
URLs) so a per-file host can be swapped in later without touching callers.

**`fluidvoice/model_download.py`** (append; keep stdlib-only):

```python
def sha256_file(path: Path) -> str          # 1 MiB chunks

def download_parakeet(name: str, progress: Progress | None = None) -> Path:
    """Fetch a PARAKEET_CATALOG model into models_dir()/parakeet/<name>/.
    Atomic model-dir semantics: any failure or abort leaves NO model dir."""
    # 1. unknown name -> ValueError (mirror download_gguf wording)
    # 2. parakeet_downloaded(name) -> return dir (noop, urlopen never called)
    # 3. stage: parakeet_dir()/f".{name}.tmp-{os.getpid()}"; first remove stale
    #    sibling f".{name}.tmp-*" dirs (crashed runs)
    # 4. tarball -> parakeet_dir()/f".{name}.tar.bz2" via existing download_file
    #    (progress passes straight through — single stream = aggregate progress)
    # 5. sha256_file(tarball) == catalog tarball_sha256, else delete tarball + raise
    #    OSError("checksum mismatch: <name> tarball — deleted, retry the download")
    # 6. tarfile.open(r|bz2): for each fname in catalog files, find the member
    #    endswith("/"+fname) (never extractall paths — read streams only),
    #    write to stage/fname; missing member -> cleanup + raise
    # 7. per-file sha256 vs catalog files[fname]; mismatch -> cleanup + raise
    # 8. os.rename(stage, parakeet_model_dir(name)); on FileExistsError (a
    #    concurrent download won) -> rm stage, return the existing dir
    # 9. unlink tarball; return dir

def download_files(entries: list[dict], dest_dir: Path,
                   progress: Progress | None = None) -> Path:
    """Generic multi-file fetch: entries = [{name, url, sha256, size}].
    Aggregate progress across files (completed bytes + current file bytes over
    sum of sizes, None total if any size unknown); per-file download_file .part
    discipline; per-file sha verify (delete + raise); everything into a staging
    dir atomically renamed at the end (same rules as download_parakeet)."""
```

All cleanup paths: `except BaseException: rmtree(stage, ignore_errors=True);
tarball.unlink(missing_ok=True); raise` — abort mid-download leaves nothing.

**Tests** — extend `tests/test_model_catalog.py` + `tests/test_model_download.py`
(fake `urlopen` exactly like the GGUF tests; build a tiny in-memory tar.bz2 with
`tarfile` `w:bz2` whose members match a **fixture catalog entry** monkeypatched
over `model_catalog.PARAKEET_CATALOG` — real checksums are too big to fake):

catalog: both keys present; every `url` starts with the release base; every
sha field is 64 hex chars; `files` ⊇ {encoder, decoder, joiner, tokens};
`features` has the seven §1.3 keys with those exact values;
`parakeet_downloaded` false → write all 4 files → true → delete one → false.

download (tarball path): happy path (progress first `(0,total)`, monotonic,
last `(total,total)`; dir holds the 4 files; no `.part`, no tarball, no stage
left); tarball-sha mismatch deletes everything and raises; mid-stream
`OSError` leaves the parakeet dir empty; extracted-file sha mismatch (inner
file tampered, tarball sha fixed up) raises and cleans; already-downloaded is a
noop; unknown name → ValueError; stale `.<name>.tmp-1` stage removed on fresh run.
download_files: two entries — aggregate progress spans both files monotonically
and ends `(sum, sum)`; one bad sha deletes that file, aborts, leaves no dest dir.

**Gate:** full suite green; nothing else in the repo references parakeet yet.

### Phase 2 — `fluidvoice/backends/parakeet_onnx.py` (module only, not wired)

Module layout (docstring carries the §1.2 contract so nobody re-derives it):

```python
"""Parakeet TDT via ONNX Runtime — numpy featurizer + greedy TDT decode. ..."""
from __future__ import annotations
import math, wave
from pathlib import Path
from typing import Any
import numpy as np
from .. import model_catalog

def slaney_mel_fb(sr, n_fft, n_mels, fmin, fmax) -> np.ndarray   # §3.1 formulas

class LogMelFeaturizer:
    """nemo-compatible log-mel (slaney mel, periodic hann, no centering)."""
    def __init__(self, sample_rate=16000, n_mels=128, n_fft=512,
                 win=400, hop=160, fmin=0.0, fmax=8000.0): ...
    def __call__(self, samples: np.ndarray) -> np.ndarray:       # (T, n_mels) f32

def load_tokens_txt(path) -> tuple[dict[int, str], int]           # id2tok, blank
def detokenize(ids, id2tok) -> str                                # ▁→space, collapse, strip

class TdtGreedyDecoder:
    """Greedy token-and-duration decode over encoder/decoder/joiner sessions
    (duck-typed: anything with .run(names, feeds)/.get_inputs/.get_outputs)."""
    def __init__(self, encoder, decoder, joiner, meta, max_tokens_per_frame=4): ...
    def run(self, feats: np.ndarray) -> list[int]                 # §1.4 loop

class ParakeetOnnxBackend:
    name = "parakeet"
    def __init__(self, cfg: dict, _sessions: Any | None = None): ...
    def warmup(self) -> None: self._load()
    def _load(self): ...        # import onnxruntime HERE, build 3 sessions
    def transcribe(self, wav_path: Path, language: str | None = None) -> dict: ...
```

Constructor: read `cfg["model"]["name"]`; `""`/`"auto"` →
`PARAKEET_DEFAULT_MODEL`; unknown name → `ValueError` listing catalog keys;
`model_catalog.parakeet_downloaded(name)` false → `RuntimeError` naming the
expected dir and pointing at Settings → Models (mirror `whisper_cpp.py:31-35`).
`_sessions` is the test seam: a callable returning the (encoder, decoder,
joiner, meta) triple; default builds real ORT sessions with
`providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider")
if p in ort.get_available_providers()]` (same `onnxruntime` import name; CUDA
EP simply shows up when the installed wheel has it — no onnxruntime-gpu extra).
`device`/`compute` config values are accepted and ignored apart from a debug log
(the provider list is the source of truth).

`_load()`: sessions; read `feat_dim/pred_hidden/pred_rnn_layers/normalize_type`
from encoder `get_modelmeta().custom_metadata_map`; build featurizer from the
catalog `features` dict (cross-check `feat_dim == n_mels`); parse tokens;
build decoder. Assert positional I/O counts (2/4/2 inputs, 2/4/1 outputs).

`transcribe`: stdlib-`wave` read (mono s16 → float32/32768; sampwidth 3
(IEEE float) accepted too); `framerate != 16000` → `RuntimeError("parakeet
needs 16 kHz mono WAV (the daemon records this; got {rate} Hz)")`; tail-pad
2 s zeros; featurize; `per_feature` CMVN when metadata says so; decode;
detokenize; return
`{"text": text, "language": None if lang in (None, "auto") else lang,
  "duration": n_samples/16000, "segments": [{"start": 0.0, "end": duration,
  "text": text}]}` (single full-take segment — word timestamps out of scope).

**Tests** — new `tests/test_parakeet_onnx.py` (no ort import anywhere):

- `TestLogMelFeaturizer`: golden cases from §1.5 (shape (8,128); the three
  pinned logmel floats tol 1e-4; silence floor `-23.02585093`; T for 560
  samples = 2; T=0 below 400; mel row 42 peak for the 1 kHz sine; plus an
  independent in-test naive reference (double loop over the §1.3/§3.1 formulas)
  asserted `allclose(feats, naive, atol=1e-4)` on a short two-tone signal).
- `TestTokens`: fixture tokens.txt with `<unk> 0`, `▁t 1`, `' 7`, `<blk> 8` →
  blank == 8; detokenize `[1, 2, 7, 3]`-style ids → expected spacing/punct;
  collapse double spaces; strip.
- `TestTdtGreedyDecoder` — stub sessions (dicts of scripted outputs keyed by
  call index; ~25 lines):
  - emission sequence covering: blank-skip (blank argmax with duration 2),
    duration-0 clamped to 1, duration-3 jump, emit at final frame → assert
    emitted ids, decoder call count, joiner call count, loop ends at `t >= T'`.
  - logits split: stub returns length `V+D` with known argmaxes → assert token
    from `[:V]` and duration from `[V:]` were used (not the tail argmax).
  - max-steps guard: joiner always returns non-blank argmax → `run` returns
    after `max_tokens_per_frame*T'` emissions, no hang.
- `TestParakeetOnnxBackend`: tmp catalog entry (monkeypatch
  `model_catalog.PARAKEET_CATALOG` to a tiny fixture with the 4 zero-byte
  files on disk) + `_sessions` stub returning scripted graphs:
  - name resolution ("auto"/""/explicit/unknown), not-downloaded RuntimeError
    mentions Settings;
  - `transcribe` on a generated 0.3 s 1 kHz s16 WAV: exact dict keys, duration
    ≈ 0.3, single segment, `language` passthrough + "auto"→None;
  - wrong-rate WAV → RuntimeError;
  - **import hygiene**: `subprocess` runs `.venv/bin/python -c "import
    fluidvoice.backends.parakeet_onnx"` then asserts `"onnxruntime" not in
    sys.modules`.

**Gate:** suite green; module still unreachable from `load_backend`.

### Phase 3 — selection + config wiring

**`fluidvoice/backends/__init__.py`**:
- docstring priority list: append
  `4. parakeet    (ONNX Runtime; explicit selection only in v1 — NOT in "auto"; divergence from upstream, see docs/STATUS.md)`.
- `load_backend`: `if wanted in ("parakeet", "parakeet-onnx"): from
  .parakeet_onnx import ParakeetOnnxBackend; return ParakeetOnnxBackend(cfg)`
  (import inside the branch, matching the file's style).
- `resolve_model_name`: after the ALIASES lookup, before the FW check —
  `from .. import model_catalog` is a cycle (model_catalog imports backends at
  module level), so import inside the function:
  `if name in model_catalog.PARAKEET_CATALOG: return name`. Whisper behaviour
  unchanged.
- `backend_status()`: add
  `status["parakeet"] = "available ({CUDA+CPU|CPU} · v2 {yes/no}, v3 {yes/no})"`
  via `_import_ok("onnxruntime")` + lazy `import onnxruntime` (version,
  `get_available_providers()` filtered) + `parakeet_downloaded` per model;
  missing → `"not installed (pip install onnxruntime)"`.

**`fluidvoice/config.py`**:
- `SETTING_ENUMS[("model", "backend")]` += `"parakeet"` (NOT the alias — the
  alias is tolerated by `load_backend` only; `apply_settings` rejects
  `"parakeet-onnx"`).
- `apply_settings` `("model", "name")` branch: accept
  `value in model_catalog.PARAKEET_CATALOG` alongside FW repos/"auto"
  (import model_catalog next to the existing lazy `backends` import).
- `ENGINE_KEYS` += `"model.name"` — REQUIRED: the parakeet Use-flow sends
  `backend`+`name`; once the backend is already parakeet, only `name` changes,
  and `_set_config` (daemon.py:806) reloads only on ENGINE_KEYS. Side effect
  (deliberate, an improvement): whisper `model.name` changes via set-config now
  hot-reload exactly like `select-model` does — no test asserts the old set.
- `TEMPLATE` `[model]` comment:
  `# auto | faster-whisper | whisper-torch | whisper.cpp | parakeet` and a
  name note (`# with backend="parakeet": parakeet-tdt-0.6b-v2 | parakeet-tdt-0.6b-v3`).

**Tests** — extend `tests/test_backends_selection.py`:
monkeypatch `ParakeetOnnxBackend` on its module (same `fakes` pattern):
explicit `"parakeet"` and alias `"parakeet-onnx"` construct it; `auto` still
resolves to whisper-family even with onnxruntime importable (fake `_import_ok`
returns True for everything; existing auto-order tests untouched and green);
unknown-backend ValueError unchanged; `resolve_model_name("parakeet-tdt-0.6b-v3")`
passes through, garbage still raises; `backend_status` with
`_import_ok("onnxruntime") → False` says not-installed, and with a stub ort
module (`sys.modules["onnxruntime"] = SimpleNamespace(__version__="1.29.0",
get_available_providers=lambda: ["CPUExecutionProvider"])` + fake catalog
downloaded flags) reports available + per-model state.
Extend `tests/test_config_settings.py`: backend enum accepts `"parakeet"`
and rejects `"parakeet-onnx"`; `model.name` accepts catalog names, still
rejects `"gpt-4o-audio"`; `ENGINE_KEYS` contains `model.name`.

**Gate:** suite green; `model.backend="parakeet"` + downloaded model now works
end-to-end through the daemon paths (validated but not yet downloadable from UI).

### Phase 4 — GTK Models group + doctor

**`fluidvoice/gtkui/settings_window.py`** (mirror the GGUF block):
- `__init__`: `self._parakeet_rows: list = []`, `self._parakeet_dl: dict = {}`.
- `_build_models`: after `gguf_group` add
  `self.parakeet_group = Adw.PreferencesGroup(title="Parakeet (ONNX)",
  description="NVIDIA Parakeet TDT via ONNX Runtime — explicit backend")`;
  add `("parakeet", "parakeet")` to the backend combo choices.
- `_refresh_models` tail: call `self._refresh_parakeet_rows()`.
- New methods (copy the GGUF names with s/gguf/parakeet/):
  `_active_parakeet()` (backend == "parakeet" and `model.name` in catalog),
  `_refresh_parakeet_rows()` (Active label / downloading spinner+subtitle /
  Use / Download & use), `_download_parakeet` (daemon thread running
  `model_download.download_parakeet(name, progress=st.update)` +
  `GLib.timeout_add(400, self._poll_parakeet_dl, name)`), `_use_parakeet`
  (`self.c.set_config({"model": {"backend": "parakeet", "name": name}})`,
  rejected-toast handling, `self._load()`, then the existing `_poll_model`
  warmup poll). Downloads run app-side, like GGUF; the daemon only validates
  config.
- Row subtitle: `f"{info['size']} · {info['langs']} · {info['note']}"`.

**`fluidvoice/doctor.py`** — `_parakeet_lines(cfg)` mirroring
`_whispercpp_lines`, printed as a `parakeet:` section after whisper.cpp:
- ort import + version (`import onnxruntime` inside try) or
  `not installed (pip install onnxruntime)`;
- available providers (and whether CUDA EP is among them);
- per catalog model: `parakeet-tdt-0.6b-v2: downloaded (<dir>)` /
  `not downloaded — missing: joiner.int8.onnx (get it in Settings -> Models)`;
- when `cfg["model"]["backend"] == "parakeet"`: resolve the configured name
  (unknown-name line listing the catalog) and mark the active model.

**Tests** — extend `tests/test_gtkui.py` (offscreen/StubClient pattern,
mirroring tests at :385-486): parakeet group rows built == catalog size with
matching titles; Active marker when cfg has backend/name set; download flow
with monkeypatched `sw.model_download.download_parakeet` → `pump_until`
done + row subtitle progress + final Use button; error path records
`w._parakeet_dl[name]["error"]`; Use flow captures StubClient.saved body
`{"model": {"backend": "parakeet", "name": ...}}`. Extend
`tests/test_infra.py`: `_parakeet_lines` for not-installed / configured+
downloaded / configured+missing-file cases (monkeypatch doctor.backends +
model_catalog like the existing whisper.cpp test).

**Gate:** suite green.

### Phase 5 — packaging + docs

- **`pyproject.toml`**: `parakeet = ["onnxruntime>=1.17"]` under
  `[project.optional-dependencies]` (comment: CPU wheel; CUDA EP is used
  automatically if the installed wheel provides it — no onnxruntime-gpu extra).
- **`docs/STATUS.md`** (Models (v0.4) block, ~:166): check the box, summary in
  the whisper.cpp style: curated sherpa-onnx tarball catalog with sha256-verified
  multi-file download, numpy log-mel + greedy TDT decode, CUDA EP when
  available; **divergence note: "auto" still prefers whisper — upstream runs
  Parakeet as its default; explicit selection only"**; default model v2
  (upstream defaults to v3).
- **`docs/UPSTREAM-TRACKING.md:59`**: row → ✅ (offline v2/v3 via community ONNX
  exports + own numpy TDT decode, no NeMo dependency); streaming rows unchanged.
- **`README.md`**: backend mentions at :51 and :269 gain parakeet; comparison
  table :233 "Parakeet on roadmap" → shipped for the parakeet backend.
- **`fluidvoice/config.py` TEMPLATE** (done in Phase 3 — listed here for the
  docs pass check).

**Gate:** suite green.

### Phase 6 — end-to-end verification (manual + optional integration)

1. Full suite: `.venv/bin/python -m pytest -q tests --ignore=tests/integration`.
2. Real-model smoke (network, once; the tarballs used for verification may
   still be under `/tmp/parakeet_dl/` — else re-download, URLs in §1.1):
   ```bash
   .venv/bin/pip install 'onnxruntime>=1.17'        # dev box only
   .venv/bin/python -c "from fluidvoice import model_download as m; \
       print(m.download_parakeet('parakeet-tdt-0.6b-v2'))"
   .venv/bin/python -m fluidvoice.cli doctor        # parakeet section sane
   .venv/bin/python - <<'EOF'                        # golden transcript check
   import tomllib, wave, numpy as np
   from pathlib import Path
   from fluidvoice import backends
   from fluidvoice.config import DEFAULTS
   cfg = {"model": dict(DEFAULTS["model"], backend="parakeet", name="parakeet-tdt-0.6b-v2"),
          "general": DEFAULTS["general"]}
   be = backends.load_backend(cfg); be.warmup()
   out = be.transcribe(Path("<tarball test_wavs>/0.wav"))
   assert out["text"] == "Well, I don't wish it any more, observed Phebe, turning away her eyes. It is certainly very like the old portrait.", out
   print("v2 OK", out["duration"], out["segments"])
   EOF
   ```
   Repeat for v3 with `en.wav`/`fr.wav` (24 kHz upstream wavs must be converted
   to 16 k mono first: `ffmpeg -i en.wav -ar 16000 -ac 1 -sample_fmt s16 en16.wav`)
   and assert the §1.5 transcripts. If a CUDA-capable wheel is available, smoke
   once with it and confirm the CUDA EP shows in doctor.
3. Optional: `tests/integration/test_parakeet_real.py` (marked `integration`,
   excluded from the default run by the existing `--ignore`): downloads v2 via
   `download_parakeet`, transcribes a bundled/generated fixture, asserts the
   golden sentence prefix; skip when the model dir already exists or network
   is unavailable.
4. UI pass (desktop session): Settings → Models shows the Parakeet group,
   Download shows progress, Use flips `model.backend` (check the socket toast
   path and `~/.config/sayit-ermano/config.toml`).

---

## 3. Appendix — exact formulas (transcribe into `slaney_mel_fb`)

### 3.1 Slaney mel filterbank (htk=False, norm='slaney')

```
def hz_to_mel(f):                       # scalar
    f_sp = 200.0/3
    if f >= 1000.0:
        return (1000.0/f_sp) + math.log(f/1000.0) / (math.log(6.4)/27.0)
    return f / f_sp
def mel_to_hz(m):                       # vectorized
    f_sp = 200.0/3
    lin = m * f_sp
    log = 1000.0 * np.exp((math.log(6.4)/27.0) * (m - 1000.0/f_sp))
    return np.where(m >= 1000.0/f_sp, log, lin)

mpts = np.linspace(0.0, hz_to_mel(fmax), n_mels + 2)
hz   = mel_to_hz(mpts)
fftf = np.fft.rfftfreq(n_fft, 1.0/sample_rate)          # 257 bins for n_fft=512
ramps = np.subtract.outer(hz, fftf)                      # (130, 257)
fdiff = np.diff(hz)
W = np.zeros((n_mels, fftf.size))
for i in range(n_mels):
    lower = -ramps[i]     / fdiff[i]        # (hz[i+1]-f)/(hz[i+1]-hz[i])
    upper =  ramps[i + 2] / fdiff[i + 1]    # (f-hz[i+1])/(hz[i+2]-hz[i+1])
    W[i]  = np.maximum(0.0, np.minimum(lower, upper))
    W[i] *= 2.0 / (hz[i + 2] - hz[i])       # slaney area norm
```

### 3.2 Framing + power

```
hann = 0.5 - 0.5*np.cos(2*np.pi*np.arange(win)/win)       # periodic
T = 1 + (N - win)//hop if N >= win else 0
frames = samples[i*hop : i*hop+win] * hann                # i in range(T)
power  = |np.fft.rfft(frames, n_fft, axis=1)|²            # zero-padded to 512
logmel = np.log(np.maximum(power @ W.T, 1e-10))
```

(The 400→512 zero-pad is magnitude-identical to torchaudio's centered window
padding — power spectra are phase-blind; verified against the real model.)

---

## 4. Out of scope (unchanged from the task brief)

Streaming Parakeet (Flash/Realtime/Nemotron), NeMo python dependency, word-
timestamp fidelity, vocabulary boosting, changing the auto backend priority,
an `onnxruntime-gpu` extra (CUDA EP is opportunistic from whatever wheel is
installed), quantized variants beyond the one int8 export per model, and
per-file mirror URLs (the `download_files` seam exists if ever needed).

## 5. Risks / notes for the builder

- **Never** name-session-I/O by string — positional binding only (§1.2).
- `model_catalog` imports `backends` at module level; `backends` must import
  `model_catalog` only inside functions (Phase 3), or you create a cycle.
- Keep `download_parakeet`'s tarball under the parakeet dir named
  `.<model>.tar.bz2` (dot-prefixed) so a crashed run's leftover is invisible to
  `parakeet_downloaded` and cleaned by the stale-stage sweep.
- ORT session options: leave thread defaults (the daemon is latency-tolerant);
  do not set `intra_op_num_threads=1` like the sherpa reference — that was for
  their benchmark isolation.
- If a future re-export changes the joiner output width, nothing in our code
  hardcodes it: `V` comes from tokens.txt, durations from `logits[V:]`.
- Golden floats in §1.5 are from numpy 2.5.2 — pin with `atol=1e-4`, not exact.
