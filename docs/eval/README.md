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
