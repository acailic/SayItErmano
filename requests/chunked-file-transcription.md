# Chunked file transcription (P3 — next product priority)

STATUS: OPEN

Source: reliability-first improvement program
(`docs/research/2026-09-10-reliability-first-improvement-program.md`, P3
backlog) — flagged there as the next product priority after P0/P1/P2.
Drafted 2026-09-10 during the P0→P2 implementation session; not started.

## Goal

Convert a long audio file once, then transcribe it in ten-minute chunks with
overlap, reconciling timestamps into one transcript — removing the 25 MB
warning path and safely raising the daemon's practical file limit.

## Requirements

1. **Convert once**: any supported input → PCM/WAV a single time (reuse the
   existing conversion utilities); slice into 10-minute chunks with a short
   1–2 s overlap (constant, documented). No per-chunk re-conversion.
2. **Reconcile timestamps**: chunk-local transcript times merge into one
   typed `Transcript` (the P1 seam, `fluidvoice/backends/base.py`) with
   global offsets; overlap-induced duplicate text stripped at boundaries
   with a conservative, documented dedup rule. Segments merged, ordered.
3. **Output shape frozen**: the CLI and control-socket `transcribe_file`
   responses keep the exact current shape (add golden compat tests).
4. **Limits**: remove the 25 MB warning path; define a new sane bound
   (disk/temp safety) enforced with a structured error, never a hang.
   Chunks process sequentially — the daemon's single-active-job busy
   guarantee is preserved.
5. **Progress**: only through an existing progress mechanism if one exists;
   do not invent new protocol surface.

## Implementation notes

- Post-P1/P2 architecture: `Daemon` is a thin composition root; the take
  path lives in `fluidvoice/capture.py` (CaptureCoordinator) and engines in
  `fluidvoice/engine_manager.py`. The file-transcription call path should be
  located fresh at implementation time (grep the current transcribe-file
  flow; it moved during the coordinator split).
- Supervised concurrency: any timers/threads must go through
  `fluidvoice/runtime_tasks.py`.
- Tests: synthetic long audio via the repo's audio-synthesis helpers;
  multi-chunk merge, overlap dedup both directions, monotonic global
  timestamps, golden output shape, oversized-file error, convert-once
  asserted via mocked conversion counting.
- Suggested home: `fluidvoice/chunking.py` + `tests/test_chunking.py`.

## Out of scope

Diarization, true streaming, n-best — all remain plan-blocked (see P3 in
the program doc).
