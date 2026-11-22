# Local evaluation harness (plan P1.4)

A backend-agnostic harness that scores the speech pipeline offline:
WER, CER, hotword recall, real-time factor, first-preview/final latency,
and hallucination-guard false positives/negatives — reported as JSON and
Markdown.

Design split, straight from the reliability plan:

- **Metric math runs without any model** (pure functions, unit-tested in
  CI). Models only *produce* the texts and latencies being scored.
- **Representative-model evaluation is a manual, required step** before
  speech-pipeline releases (see §Release policy) — the harness provides
  the machinery, the release process provides the discipline.

## Quickstart

```bash
# validate the committed synthetic corpus + audio presence
python -m fluidvoice.evalharness check
# same thing through the app CLI (args pass straight through)
sayit-ermano eval-run check

# materialize the synthetic fixture wavs (regenerated, never committed)
python -m fluidvoice.evalharness fixtures

# smoke-run the harness itself (stub transcriber, empty transcripts —
# proves the plumbing; NOT an evaluation, prints a loud warning)
python -m fluidvoice.evalharness run --out /tmp/eval-smoke

# summarize a soak CSV (scripts/soak.py) on its own
python -m fluidvoice.evalharness soak /tmp/sayit-ermano-soak.csv

# smoke-run the AI-polish comparison + regression gate (no adapters:
# reference passthrough — proves the plumbing; NOT an evaluation)
python -m fluidvoice.evalharness polish-compare --out /tmp/polish-smoke
```

Every subcommand takes `--manifest PATH` (repeatable; a file or a
directory containing `manifest.toml`; default: the packaged synthetic
corpus) and `--external PATH` (repeatable; merged in, see
§Private corpora). `run` writes `report.json` + `report.md` to `--out`.

## Corpus, manifests, fixtures

Cases are described by TOML manifests — the full field reference and
validation rules live in [MANIFEST-SCHEMA.md](MANIFEST-SCHEMA.md).
The committed corpus (`fluidvoice/evalharness/corpus/manifest.toml`) is
**CC0-1.0 by construction**: its audio is synthesized tones/silence, no
recorded speech, regenerated from per-case `synth` recipes
(`fixtures` or any `run` materializes them; identical bytes every time).
No binary fixture is ever committed — nothing in git can carry a
license you didn't intend to redistribute.

### Private corpora

Keep private/non-redistributable corpora out of git entirely:

```bash
mkdir eval-private          # audio + manifest.toml live here
echo "eval-private/" >> .gitignore   # pattern recommended in-repo

python -m fluidvoice.evalharness run \
    --manifest <packaged-or-committed-manifests...> \
    --external eval-private/manifest.toml \
    --transcriber my_adapter:make_transcriber \
    --out eval-report-$(date +%F)
```

Merged corpora must not repeat a case `id` — duplicates are an error,
not a silent overwrite. Every case's `license`/`source` must be filled
in, even in private manifests: the point is that the *committed* corpus
stays provably redistributable.

## Metrics and conventions

All conventions are pinned by unit tests (`tests/test_evalharness*.py`,
hand-computed cases) and restated in every `report.json`.

| metric | definition |
|---|---|
| WER | word-level Levenshtein distance / reference words |
| CER | character-level Levenshtein / reference chars |
| hotword recall | fraction of the case's tags (that the reference contains) present in the hypothesis |
| real-time factor | audio duration / processing time — **> 1 = faster than realtime** |
| first-preview latency | seconds until the first preview text (transcriber-reported) |
| final latency | seconds until the final transcript (transcriber-reported) |
| guard FP / FN | expected `ok` but flagged / expected `flag` but passed |

- **Text normalization** (WER/CER): NFKC, casefold, tokens of
  `[\w']+` (apostrophes only inside words), single spaces. Case and
  punctuation never count as errors; spaces *are* characters for CER.
- **Real-time factor** is the reciprocal of the ratio some tools call
  RTF (processing/audio); reports state the direction so numbers are
  never ambiguous.
- Empty reference + empty hypothesis scores 0.0; empty reference with
  non-empty hypothesis scores 1.0. Cases with nothing scoreable are
  `null` in JSON / `n/a` in Markdown and excluded from means.
- Guard expectations of `none` exclude a case from guard scoring; a
  transcriber that never ran its guard is counted as `not-run`, not as
  right or wrong.

## Plugging a real backend (manual step)

The runner takes any transcriber callable
`transcriber(audio_path, case) -> {"text": str, ...}` and never imports
backends itself. A representative-model evaluation wraps the real
backend in a ~15-line adapter module:

```python
# my_adapter.py — run from a checkout with the model installed
from pathlib import Path
from fluidvoice import backends
from fluidvoice.config import load_config

def make_transcriber():
    cfg = load_config()
    backend = backends.load_backend(cfg)
    lang = backends.effective_language(cfg, backend)

    def transcribe(audio_path: Path, case):
        t0 = time.perf_counter()
        result = backend.transcribe(audio_path, language=lang)
        return {
            "text": result["text"],
            "guard": guard_outcome_for(result),   # your guard wiring
            "final_latency_s": time.perf_counter() - t0,
        }

    return transcribe
```

```bash
python -m fluidvoice.evalharness run \
    --external eval-private/manifest.toml \
    --transcriber my_adapter:make_transcriber \
    --soak-csv /tmp/sayit-ermano-soak.csv \
    --out eval-report-v0.9.0
```

Optional result keys (unknown keys are kept verbatim under `raw` in the
report, next to the computed metrics): `guard` (`ok`/`flag`/`none`),
`first_preview_latency_s`, `final_latency_s`, `processing_time_s`
(defaults to the runner's wall-clock around the call). A case whose
transcriber raises is recorded with its error and excluded from
aggregates — one bad case never kills the run, but `run` exits 1 if any
case errored.

## Reports

`report.json` carries `summary` (means + guard confusion counts),
`cases` (per-case metrics, guard category, raw transcriber output,
errors), `conventions`, and optionally `soak`. `report.md` is the same
data as a readable table (summary table + per-case table + soak block).
Keep the report directory with the release notes when the release
policy below requires it.

## Soak integration and release policy

`scripts/soak.py` samples the running daemon's RSS/CPU into a CSV;
the harness summarizes it (`soak` subcommand) and embeds it in reports
(`run --soak-csv`). Policy, wired into the release gates
([../dev/release-gates.md](../dev/release-gates.md)):

- **Before every speech-pipeline release** (any change to backends,
  decoding, the hallucination guard, or transcript post-processing):
  a manual representative-model evaluation over a corpus that includes
  real speech for your languages, plus a **2-hour soak**.
- **For lifecycle changes** (idle-unload, model reload/hot-swap, memory
  work): a documented **24-hour soak** — keep the CSV next to the
  evaluation report and reference both in the release notes.

Soak verdicts stay human: the summary reports RSS start/end/drift/max
and CPU-seconds-per-hour; whether +40 MB over 24 h is a leak is a
judgment call the numbers inform, not one the script makes.

## Polish quality gate (local vs cloud, #21)

`polish-compare` scores what happens AFTER transcription: how each
AI-polish variant transforms the raw transcript, against the same
corpus references, with the same metric math as `run` (WER / CER /
omission / hotword recall — no second definitions). It exists so the
bundled local polish (`ai.local_polish`, the managed llama.cpp server)
can be held to the cloud baseline before a release: **local polish
must not degrade output vs the cloud corpus** — the regression gate
fails (exit 1) when any polish call errors, when
`wer_mean(local) - wer_mean(cloud) > --max-wer-degradation`
(default 0.02), or when hotword recall drops by more than
`--max-recall-loss` (default 0).

Flow per case: obtain the RAW transcript through `--transcriber` (no
flag = reference passthrough SMOKE mode, loudly noted — like the stub
transcriber in `run`), then run every polish variant plus the built-in
`raw` identity baseline. Output: `polish-report.json` +
`polish-report.md` in `--out` (default `./eval-polish`) with
per-variant means, deltas vs raw, per-case rows, and the gate verdict.

Adapters are `module:factory` callables `(raw_text, case) -> {"text":
str, ...}` — a ~10-line module covers each side:

```python
# polish_adapters.py — run from a checkout with the model + binaries
from fluidvoice.config import load_config
from fluidvoice.local_llm import LocalPolishManager
from fluidvoice.runtime_tasks import RuntimeTasks

def make_local():
    cfg = load_config()
    cfg["ai"]["local_polish"] = True
    mgr = LocalPolishManager(cfg, RuntimeTasks())
    def polish(text, case):
        return {"text": mgr.polish_text(text)}
    return polish

def make_cloud():
    from fluidvoice.ai.client import AIClient
    cfg = load_config()  # your cloud [ai] endpoint + key env
    client = AIClient(cfg)
    def polish(text, case):
        return {"text": client.polish(text)}
    return polish
```

```bash
python -m fluidvoice.evalharness polish-compare \
    --external eval-private/manifest.toml \
    --transcriber my_adapter:make_transcriber \
    --local polish_adapters:make_local \
    --cloud polish_adapters:make_cloud \
    --out eval-polish-$(date +%F)
```

Release policy: **before any change to the local-polish defaults**
(model choice, server flags, prompts routed through the local path),
run the comparison over a corpus with real speech for your languages
and keep `polish-report.*` next to the release notes — same discipline
as the STT gate above. Offline E2E note: with the network disconnected,
the `local` variant must still score (that is the feature's promise);
the `cloud` variant's numbers can come from an earlier online run of
the same corpus/adapter pair.

## Perf baseline + regression gate (Q8)

The perf half of quality-plan Q8 lives on the same corpus, one level
up in abstraction: instead of scoring *transcripts*, it scores
*latency and footprint*. `scripts/perf_baseline.py` (via
`just perf-baseline`) runs the committed synthetic corpus through a
real backend in the daemon process shape — a dedicated worker
subprocess loads the model once and takes every case — recording
per-take wall time, audio-seconds/second throughput, model load time
and the worker's peak RSS into a dated evidence file under
[../quality/perf-baselines/](../quality/perf-baselines/) plus a
generated Markdown summary table (the install-matrix pattern:
generated, never hand-edited). `scripts/perf_gate.py`
(`just perf-gate`) re-runs the same measurement and fails (exit 1)
when any metric regresses beyond the tolerance — default 25 % per
metric, `--tolerance` to change; a corrupted newest baseline is an
error, never silently skipped.

```bash
just perf-baseline            # record <date>-<host-class>.json + table
just perf-gate                # fresh run vs the newest baseline
python scripts/perf_gate.py --tolerance 0.10   # tighter gate
```

Backend selection follows the app's preference (faster-whisper CPU
int8, falling back to the whisper.cpp binary + tiny ggml model baked
into reference runners) and is stamped in every evidence file, so a
baseline recorded on one backend never silently gates another. The
measurement/aggregation math, the tolerance comparison, baseline
resolution and malformed-baseline rejection are model-free and pinned
by `tests/test_evalharness_perf.py` with a fake backend — same split
as the rest of this harness: models only produce the numbers.
