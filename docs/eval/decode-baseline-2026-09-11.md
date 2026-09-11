# TTS decode baseline — first real-model measurement

- Date: 2026-09-11 (run at 13:59:59 UTC)
- Status: **EVIDENCE — one machine, one run, clearly caveated**
- Origin: wave-3 brief, agent J ("real-model decode benchmark: TTS speech
  + negatives"). This is NOT the human corpus — the corpus brief
  ([corpus-spec](corpus-spec.md)) stays in effect and OPEN.
- Tooling: `fluidvoice/evalharness/tts_bench.py` + the wave-2 measurement
  adapters (runner/metrics, G1–G5 as wired below). Reference set:
  `fluidvoice/evalharness/corpus/tts-reference.toml` (32 speech + 5
  negatives, versioned).
- Raw artifacts: `~/.cache/sayitermano-bench/` — `report-forced/`,
  `report-auto/`, `summary.md`, `audio/` (audio stays outside git).

## 1. Environment

| what | value |
|---|---|
| speech backend | **faster-whisper** (app's auto resolution via `backends.load_backend`, in-memory mirror of the daemon's `[model]`/`[general]`: `backend=auto`, `name=large-v3`, `device=auto`→cpu, `compute=auto`→int8, `language=en`) |
| why not whisper.cpp | the wave-3 brief named the whisper.cpp ggml model (`~/.cache/openwhispr/whisper-models/ggml-large-v3-turbo.bin`, present on disk) — but **no `whisper-cli` binary exists on this machine** (verified: none of `whisper-cli`/`whisper-cpp`/`whisper.cpp`/`whisper-main` on PATH or filesystem). The app's own resolution therefore constructs faster-whisper large-v3 — the same backend the production daemon uses for every real dictation (`history.jsonl` `backend` field). The ggml path was recorded, not used. |
| CPU | AMD Ryzen 7 5700X (8c/16t), CPU-only decode (no CUDA) |
| load caveat | production daemon **active** during the run (per worktree rules it is never stopped) + desktop session; loadavg ≈ 3.5 at start. Latencies are a light-load measurement, not an idle-machine one. |
| TTS | piper `en_US-lessac-medium` (22050 Hz → ffmpeg → 16 kHz mono s16, the production byte format), run from a private bench venv (`~/.cache/sayitermano-bench/venv`) — the stale `~/.local/bin/piper` shim imports the wrong `piper` package and is not used |
| negatives | digital silence 2/5/10 s + deterministic low noise (−43 dBFS peak) 5/10 s, generated with the stdlib `wave` module (seeded; no network) |
| run cost | forced pass 257 s wall, auto probe 203 s wall, TTS generation ~90 s; 0 generation failures; **runtime guard not triggered** (no utterance cut; all 32 ran) |

## 2. Method

1. Render each of the 32 reference utterances (stratified like
   corpus-spec §4 at 1/10th scale: 8×T1 commands, 6×T2 messages, 7×T3
   jargon, 6×T4 names/numbers, 1×T5 long-form ~22 s, 4×T6 dev prompts)
   with piper.
2. Generate the 5 negatives (silence/noise, empty reference).
3. **Forced pass** (production mirror): every case decoded with the
   backend constructed exactly as the app constructs it
   (`load_backend(cfg)` + `warmup()`, mirroring the daemon's eager
   warmup), language forced to the daemon's `general.language` = `en`.
   This is the configuration users get.
4. **Auto probe**: a fixed subset (first 2 cases of each short taxonomy,
   10 speech cases) + all negatives re-decoded with `language=auto` —
   language-detection sanity (G5) and the classic
   auto-detect-on-silence hallucination trigger (G3).
5. Score with the wave-2 adapters: WER/CER, omission rate (G2),
   hallucination words/s on negatives (G3), punctuation family (G4),
   language match (G5), subgroup tables per taxonomy (G1), RTF
   (audio/processing, **>1 = faster than realtime**), final latency.
   Guard expectations are recorded but **the hallucination guard does
   not run** (it is a pipeline feature above the backend; all guard
   cells read "not run" in the raw reports).
6. Latency = wall time around one in-process `transcribe()` call (model
   already warmed; one-time model load ≈ 16 s excluded, as in the
   daemon). **First-preview latency is not wired** — preview is a
   daemon-pipeline feature above this seam.

Rerun (from the repo root; ~10 min on this machine):

    /home/nistrator/Documents/github/FluidVoiceLinux/.venv/bin/python \
        -m fluidvoice.evalharness.tts_bench \
        --workdir ~/.cache/sayitermano-bench

(`--limit N` is the runtime guard, minimum 15; the cut is recorded.)

### Caveats — read before quoting any number

- **Single TTS voice**, one speaker style, US English only. No accents,
  no cross-speaker variance, no microphone chain: piper's clean 22 kHz
  neural voice is *easier* than real dictation audio. This is a
  baseline floor, not a quality claim.
- **TTS round-trip confound**, strongest in T4: the reference is the
  text fed to piper; numbers/dates are *spoken* by piper and may come
  back digit-vs-word ("thirty"→"30") or re-segmented ("3.14.2"→
  "3.1.4.2"). Those are word errors under the harness conventions but
  partly the TTS's pronunciation choice, not pure decode failure.
- **Machine-clean audio** (no noise, no room reverb, no mic EQ): SNR
  far above the corpus-spec §5 targets. Real-corpus WER will be higher.
- **Backend-level measurement**: the app's post-processing (filler-word
  removal, punctuation pipeline, hallucination guard, hotword hints,
  preview) is NOT applied. Numbers are the model's raw behavior.
- **One machine, one run, light-moderate load** (daemon active). No
  error bars; do not treat 2.8% vs 3% differences as signal.
- The harness's corpus-level WER means in `summary.md`/`report.json`
  **include the 5 negatives at WER 1.0 by convention** (empty reference
  + any output = 1.0) — that is why summary.md's headline reads 0.159.
  The speech-only numbers below are computed from the per-case rows.

## 3. Results — forced pass (production mirror, language=en)

Speech-only, n=32 (negatives scored separately in §3.3):

| metric | mean | min | max |
|---|---|---|---|
| WER | **0.028** | 0.000 | 0.286 |
| CER | 0.005 | 0.000 | 0.046 |
| omission rate (G2) | **0.000** | 0.000 | 0.000 |
| hotword recall | 0.909 | 0.000 | 1.000 | (n=11 scoreable: T3/T4 tags) |
| RTF (>1 = realtime+) | 0.468 | 0.170 | 2.075 |
| final latency s | 6.64 | 6.14 | 10.51 |

27/32 utterances decoded **perfectly** (WER 0). Every error case is a
single-token error (see §4).

### 3.1 Per-taxonomy subgroups (G1)

| taxonomy | n | WER | CER | omissions | hotwords | RTF | final s |
|---|---|---|---|---|---|---|---|
| t1-command | 8 | **0.000** | 0.000 | 0.000 | n/a | 0.214 | 6.19 |
| t2-message | 6 | 0.064 | 0.007 | 0.000 | n/a | 0.414 | 6.61 |
| t3-jargon | 7 | 0.024 | 0.005 | 0.000 | 1.000 | 0.374 | 6.42 |
| t4-names-numbers | 6 | 0.048 | 0.005 | 0.000 | 0.833 | 0.473 | 6.55 |
| t5-longform (22 s) | 1 | **0.000** | 0.000 | 0.000 | n/a | **2.075** | 10.51 |
| t6-dev-prompt | 4 | 0.011 | 0.011 | 0.000 | n/a | 0.811 | 7.19 |

Long-form is the only case that decodes faster than realtime
(RTF 2.075): short takes are dominated by a **~6 s fixed per-take cost**
(latencies cluster at 6.1–6.9 s for 1.5–7 s clips; the 22 s clip took
10.5 s — roughly 6 s + 0.2×audio). On this CPU a short dictation waits
mostly on fixed decode overhead, not audio length.

### 3.2 Punctuation family (G4, raw text)

| metric | mean (n=32) |
|---|---|
| terminal punctuation accuracy | 0.922 |
| sentence-start capital accuracy | 0.953 |

### 3.3 Negatives (G3) — hallucination on silence/noise

Forced-en (production condition): **every** negative produced output —
exactly `"Thank you."` (2 tokens) on all five, regardless of length or
silence-vs-noise. 5/5 hallucinated, but tiny and constant.

| negative | tokens | audio s | halluc words/s |
|---|---|---|---|
| neg-silence-2s | 2 | 2.0 | 1.000 |
| neg-silence-5s | 2 | 5.0 | 0.400 |
| neg-silence-10s | 2 | 10.0 | 0.200 |
| neg-noise-5s | 2 | 5.0 | 0.400 |
| neg-noise-10s | 2 | 10.0 | 0.200 |

Auto probe: hallucinations **double** (4 tokens) and get stranger —
subtitle-train artifacts:

| negative | hypothesis | detected lang | halluc words/s |
|---|---|---|---|
| neg-silence-2s | "Teksting av Nicolai Winther" | **nn** | 2.000 |
| neg-silence-5s | "Teksting av Nicolai Winther" | nn | 0.800 |
| neg-silence-10s | "Undertexter av Nicolai Winther" | nn | 0.400 |
| neg-noise-5s | "Thank you for watching." | nn | 0.800 |
| neg-noise-10s | "Thank you for watching." | nn | 0.400 |

("Teksting/Undertexter av Nicolai Winther" ≈ "Subtitling by Nicolai
Winther" — a known Whisper training-data artifact, here in Norwegian
Nynorsk.)

### 3.4 Language detection (G5, auto probe)

| expected | detected | match | cases |
|---|---|---|---|
| en | en | yes | 10/10 speech |
| en | **nn** | NO | 5/5 negatives |

Detection on speech is perfect (10/10 en, including accented-less TTS
jargon). On silence/noise, auto-detect collapses to `nn` for all five —
production forcing `en` avoids the wrong-language branch entirely.

### 3.5 Auto vs forced (shared 10-case subset)

Identical transcripts and identical errors in both passes (the three
error cases below repeat 1:1); auto costs ~2× latency (mean 12.4 s vs
6.5 s on the subset — detection adds ≈ 6 s, consistent with the fixed
per-take cost doubling).

## 4. Top-3 error patterns (all 5 error cases, forced pass)

1. **Number/format round-trip** (2 of 5 errors, both TTS-confounded —
   see caveat): `Deploy version 3.14.2 to production.` →
   `…version 3.1.4.2 to production.` (WER 0.286, the T4 hotword miss);
   `…older than thirty days.` → `…older than 30 days.` (word-vs-digit).
2. **Compound spelling**: `…after the standup tomorrow.` →
   `…after the stand-up tomorrow.` (WER 0.286 — a 1-substitution-in-7
   short case; CER 0.023 shows how near the miss is).
3. **Short-function-word near-homophones** on clean speech:
   `Start without me.` → `Tart without me.`; `Git rebase onto…` →
   `Get rebase onto…` (WER 0.167).

**No omissions anywhere** (G2 = 0.000 on all 32 cases and 0.000 in the
auto subset) and no multi-word derailments: on machine-clean speech the
model's failure mode is single-token near-misses, not dropped content.

## 5. What this does NOT claim

- Not a human-speech WER number: single synthetic voice, English, clean
  audio. The human corpus (corpus-spec) remains the instrument for any
  user-facing quality claim; this baseline only says the decode path
  works and how it fails *in its easiest condition*.
- Not a pipeline number: filler/punctuation/guard/hotwords/preview are
  app features above the backend seam and are not applied here.
- Not a latency SLA: one loaded desktop, one run, CPU int8; the ~6 s
  fixed per-take cost and RTF figures are evidence for where
  optimization matters, not guarantees.
- Not the whisper.cpp measurement the brief asked for: no `whisper-cli`
  binary exists on this machine; the ggml large-v3-turbo file remains
  unmeasured (a future bench could add a whisper.cpp backend install).
- Guard outcomes are unmeasured (guard not run at this seam) — the
  negatives' G3 rates stand on their own.

## 6. Implications worth carrying forward (observations, not decisions)

- The hallucination-on-silence result (100% of negatives emit text, tiny
  under forced-en, doubled + wrong-language under auto) is direct
  evidence for the pipeline guard's value and for keeping production on
  forced language.
- The ~6 s fixed per-take decode cost dominates short-dictation latency
  on this CPU — preview/segmentation work and model-load behavior are
  where latency wins live.
- T4-style number handling (hotword hints, formatting normalization) is
  the clearest decode-adjacent gap visible even in this easy condition.
