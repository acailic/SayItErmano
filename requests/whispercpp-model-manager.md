Plan the implementation of whisper.cpp model auto-download and a model manager - UPSTREAM-TRACKING row "whisper.cpp GGUF auto-download; model manager" (docs/STATUS.md "not started" list).

STATUS: SHIPPED

<!-- shipped in ae0fc66 -->

Today: the whisper.cpp backend (fluidvoice/backends/whisper_cpp.py) requires model.whispercpp_model to be a manual local path to a ggml/gguf file, and the Models section of the GTK app (fluidvoice/gtkui/settings_window.py + fluidvoice/model_catalog.py) only knows faster-whisper models. Goal: choosing and using whisper.cpp becomes as easy as faster-whisper.

Scope:
1) GGUF catalog in model_catalog.py: a curated dict of whisper.cpp GGUF models (ggml-base.bin / ggml-base.en.bin / ggml-small.bin / ggml-small.en.bin / ggml-medium.bin / ggml-medium.en.bin / ggml-large-v3.bin from huggingface ggerganov/whisper.cpp releases) with approximate sizes and a direct download URL each; `gguf_downloaded(name)` checking paths.models_dir()/whisper.cpp/.
2) Downloader in a new fluidvoice/model_download.py: streaming HTTP download (stdlib urllib) to models_dir with a .part temp file renamed on success, resumable not required, progress callback (bytes/total) the UI can poll; sha256 verification is NOT required v1.
3) Config: model.whispercpp_model accepts either a path (unchanged) or a catalog name; the whisper.cpp backend resolves names to models_dir()/whisper.cpp/<file> and errors clearly when the file is missing (name + hint to download).
4) Model manager UI: the GTK Models section gains a "whisper.cpp GGUF" group - rows per catalog entry (name, size, downloaded check, Download button with progress, Use button writing model.whispercpp_model and model.backend="whisper.cpp" via the daemon set-config socket action); downloads run in a worker thread, UI polls progress; the daemon gains a socket action `download-model` {kind: "gguf"|"fw", name} that runs the download in a thread and reports progress via the existing status/warmup-ish channel - OR the app downloads directly since it shares the filesystem; the plan should pick ONE owner (prefer direct app-side download; daemon only validates config) and justify it.
5) doctor.py: report whisper.cpp model resolution (binary + resolved model path or catalog hint).

Where: fluidvoice/model_catalog.py, new fluidvoice/model_download.py, fluidvoice/backends/whisper_cpp.py, fluidvoice/gtkui/settings_window.py, fluidvoice/doctor.py, config docs. Suite: `.venv/bin/python -m pytest -q tests --ignore=tests/integration` (439 green at HEAD 6981a2d).

Done means: a phased, file-level plan under `specs/` a builder can implement without questions - each phase leaves the suite green; unit tests with mocked urllib (progress callback, .part rename, failure leaves no corrupt final file), catalog completeness, backend name resolution (path passthrough vs catalog name vs missing file error), and UI wiring smoke tests like the existing gtkui tests. No real network in tests.

Out of scope: sha256/checksums, resumable downloads, converting faster-whisper models, torch backend models, quantization variants beyond the curated list, remote model listings (fixed catalog only).
