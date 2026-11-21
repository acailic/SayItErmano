Bundled local AI polish — one-click llama.cpp server + GGUF chat model (issue #21). A paying user without a cloud key gets polished output with zero setup, offline end to end.

STATUS: SHIPPED

<!-- shipped on feat/local-ai-polish (catalog+downloads+supervisor+config
     e8b0..: model_catalog/model_download, ai/local.py, config keys,
     AIClient override, daemon wiring + local-ai routes, settings UI,
     evalharness polish bench + corpus + CLI; live-verified on the repo
     CI machine 2026-11-21: real 2.5 GB qwen3-4b Q4_K_M download
     (sha256 pin validated), llama.cpp b12606 spawn->health 2.5-3 s,
     polish "um lets meet on tuesday around 3 no wait 4 p.m." ->
     "Let's meet on Tuesday at 4 p.m." character-exact; bench: local
     similarity 0.866 / recall 0.874 vs no-polish floor 0.628/0.846,
     0 hard failures (docs/eval/polish-baseline-2026-11-21.md). Cloud
     side of the gate not run here (no cloud key on that machine) -
     run it wherever a key lives; the gate compares any two providers
     on the same corpus. -->

Design (2026-11-21):

1) Catalog + downloads (`model_catalog.py` / `model_download.py`):
   CHAT_CATALOG (qwen3-4b-instruct-2507-q4_k_m, Q4_K_M, 2.5 GB,
   sha256 = HuggingFace LFS oid; unsloth mirror because the official
   Qwen GGUF repo is gated) + pinned llama.cpp runtime b12606 (official
   ubuntu x64/arm64 tarballs, GitHub asset digests; safe per-member
   extraction incl. in-tree SONAME symlinks; exec bits). One-click
   download_chat_stack = model + runtime with aggregate progress;
   cached_models/cache_entry_path gain llama.cpp kinds (deletion through
   the existing daemon route, guarded).

2) Supervisor (`fluidvoice/ai/local.py`): LocalServer - spawn on demand
   (Settings/AI test or the daemon's pre-polish ensure), /health wait,
   idle-stop watcher (ai.local_idle_stop_s), fixed loopback port
   (ai.local_port, default 8931) so every consumer derives the same
   base_url without discovery; stop() on toggle-off/shutdown; RSS via
   procfs for the status surface; a llama-server on PATH is a fallback.

3) Config: ai.local_polish / local_model / local_port /
   local_idle_stop_s - all hot-applied; the AIClient seam redirects
   base_url/model while on and NEVER sends the cloud API key to the
   local server. ai.enabled stays the master switch; the Models-page
   one-click sets both.

4) Daemon: _ensure_local_ai before dictate/rewrite/command takes
   (failure = log + notify + raw-transcript fallback, never a dropped
   dictation); apply_config stops the server on toggle-off (zero
   footprint enforced by tests); status.local_ai block; local-ai-start
   / local-ai-stop routes (Settings test/status rows).

5) Settings UI: AI page "Local polish (works offline)" group (toggle,
   model combo, idle-stop minutes, live server status + Start & test /
   Stop now); Models page "Local AI polish (chat GGUF)" group with the
   one-click Download & use.

6) Eval gate (fluidvoice/evalharness/polish.py + corpus/
   polish-baseline.toml + `polish` CLI subcommand): 16 authored
   disfluent->polished cases; similarity (1-CER) + content recall
   (reference-word survival) + exact + hard failures (empty/refusal);
   gate = local within margin (default 0.05) of cloud on both means and
   zero hard failures, non-zero exit on FAIL. Evidence:
   docs/eval/polish-baseline-2026-11-21.md; guide:
   docs/guides/local-polish.md (budgets documented + enforced).

Out of scope: GPU-offload tuning (the pinned runtime is the CPU build;
point ai.base_url at a GPU llama.cpp/Ollama for that), more chat models
in the catalog (the plumbing is generic; add entries as they are
gated), a llama.cpp download for non-x86_64/arm64 platforms (PATH
fallback covers them), automatic re-gating in CI (the gate needs a
cloud key; documented as a release-check command instead).
