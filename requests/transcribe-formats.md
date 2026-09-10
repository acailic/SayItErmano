Plan the upgrade of `fluidvoice transcribe` (one-shot file transcription) - the UPSTREAM-TRACKING row "File transcription: chunked API uploads, .opus/.oga input" and the speaker-labeling prerequisite "timestamps/JSON export would come with it".

STATUS: SHIPPED

<!-- shipped in 6981a2d -->

Scope v1:
1) Input formats: today the README claims wav/flac/mp3 but opus/oga are unverified. faster-whisper decodes via PyAV, so most formats likely already work - the plan must first VERIFY which extensions actually decode, then add an explicit ffmpeg fallback (fluidvoice/audio_utils.py: `ensure_wav(path) -> Path` converting anything ffmpeg knows to 16k mono wav when the direct decode fails; error message names ffmpeg when it is missing), and accept the verified extension list in the CLI/docs.
2) Structured output: `fluidvoice transcribe FILE --json` prints {text, language, duration_s, segments: [{start, end, text}]} (segments from the faster-whisper segment iterator; empty segments list for backends that do not expose them), and `--out PATH` writes the transcription to a file instead of stdout. Keep plain-text default output unchanged.
3) Long-file sanity: no chunking in v1, but the output must not double-load the file (transcribe once, reuse segments for both text and JSON), and a >25 MB input gets a friendly warning suggesting ffmpeg conversion.

Where: fluidvoice/cli.py (transcribe args), fluidvoice/audio_utils.py (ensure_wav), fluidvoice/backends/ (segment exposure - check what transcribe() returns today and extend the faster-whisper backend minimally; torch/whisper.cpp backends may return segments: [] with a note). Suite: `.venv/bin/python -m pytest -q tests --ignore=tests/integration` (green at the then-current HEAD).

Done means: a phased, file-level plan under `specs/` a builder can implement without questions - each phase leaves the suite green; unit tests for ensure_wav (passthrough for wav, ffmpeg invocation with mocked subprocess, missing-ffmpeg error) and the CLI --json/--out paths (capsys + tmp files, stub backend); the format-verification findings recorded in the plan (which extensions PyAV decodes on this machine) so docs/README state only verified formats.

Out of scope: chunked API uploads, diarization/speaker labels, streaming, remote URLs, GPU model changes.
