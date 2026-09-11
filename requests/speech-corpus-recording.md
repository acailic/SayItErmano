# Real-speech corpus recording + measurement adapters gap list (phase 1)

STATUS: OPEN

Source: [phase 0 report](../docs/research/2026-09-11-phase0-report.md)
finding F-14/F-13. Specification:
[corpus-spec.md](../docs/eval/corpus-spec.md). This brief records the
corpus and enumerates (not implements) the evalharness adapters.

## Goal

A consented, licensed corpus of 150–300 real utterances across 10–15
speakers (plus separate silence/noise cases) with a held-out split, and
a written gap list of measurement adapters the existing evalharness
needs before any "trustworthy speech" claim is measurable.

## Requirements

1. Execute the corpus spec: recruit per its stratification (audience
   languages, non-native English, mic classes), record with consent,
   keep private audio OUTSIDE git; store provenance metadata in-repo.
2. Held-out set (≥20%) sealed and untouched by any guard/threshold work.
3. Produce `docs/eval/corpus-report.md`: coverage vs the spec taxonomy,
   per-stratum counts, recording quality notes, and the adapter gap
   list (WER/CER exist; specify missing: omissions, hallucination rate,
   guard FP/FN, VAD false-stops, punctuation/meaning errors, and the
   RTF sign/definition check already flagged in the spec).
4. No guard tuning, no backend changes — measurement only.

## Out of scope

Adapter implementation (follow-up briefs per gap), benchmarking runs
(phase 1 measurement once adapters exist), corpus redistribution
decisions (rights inventory is the commercial track's job).
