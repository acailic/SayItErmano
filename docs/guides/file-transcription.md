# File transcription (`sayit-ermano transcribe`)

Part of the [documentation index](../README.md). One-shot
transcription of audio files on disk — formats, JSON output,
chunking.

Accepts **wav, flac, mp3, opus, oga, ogg, m4a, aac, wma, aiff, webm** (verified
to decode via PyAV). Unknown extensions are still attempted: anything PyAV
can't open is converted with **ffmpeg** to 16 kHz mono WAV first
(`sudo apt install ffmpeg` if it's missing). The whisper.cpp backend always
converts via ffmpeg since `whisper-cli` reliably reads WAV only.

- `--json` prints `{text, language, duration_s, segments}` where `segments`
  are raw `{start, end, text}` per-segment entries with timestamps
  (not post-processed; `[]` on the whisper.cpp backend — segment parsing
  isn't wired up there in v1).
- `--out PATH` writes the result to a file instead of stdout (JSON with
  `--json`); missing parent directories are created.
- Long inputs are **chunked** (P3): anything over ten minutes is converted
  once, transcribed in ten-minute overlapping chunks, and reconciled into
  one transcript (boundary duplicates deduplicated conservatively — exact
  text match within the 1.5 s overlap window). The old >25 MB warning is
  gone; the bound is decoded duration — 6 h of audio max (disk/temp safety),
  enforced with a structured error, never a hang.

