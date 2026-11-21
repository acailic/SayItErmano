# Local AI polish (works offline)

The optional AI polish step can run **entirely on your machine**: a
bundled [llama.cpp](https://github.com/ggml-org/llama.cpp) server with a
small GGUF chat model, downloaded once, started by the daemon only when
a dictation needs it and stopped again after idle. No cloud key, no
account, nothing leaves the machine — the same local-first promise as
the speech transcription, now end to end.

## One click

1. **Settings → Models → Local AI polish (chat GGUF)** →
   **Download & use** (downloads the chat model + the llama.cpp server,
   ~2.6 GB total, checksum-pinned) — or pick **Use** if it is already
   downloaded.
2. Dictate. The first take with polish pays the model load
   (~3–15 s on CPU); later takes ~1–3 s.

The button turns on **Local polish** (and the AI polish master switch)
in Settings → AI. Turn it off there at any time — the cloud endpoint
settings are never touched, so toggling back is instant.

## What runs where

| Piece | Location | Notes |
|---|---|---|
| chat model (GGUF) | `~/.cache/sayit-ermano/models/llama.cpp/` | Qwen3-4B-Instruct-2507 Q4_K_M, sha256-pinned (LFS oid) |
| llama.cpp server | `~/.cache/sayit-ermano/models/llama.cpp/runtime/b12606/` | official release build, GitHub asset digest verified; a `llama-server` on PATH is used as fallback |
| network surface | `127.0.0.1:<local_port>` only | default port 8931; no API key is ever sent to it |

The daemon (not the app) owns the server process: spawn on demand →
wait for `/health` → polish → idle-stop. If you already run Ollama,
LM Studio or your own llama.cpp server, leave Local polish **off** and
point the regular AI polish endpoint at it — nothing changes.

## Budgets (documented and enforced)

| State | Disk | RAM | Process |
|---|---|---|---|
| toggle off / model not downloaded | 0 | 0 | none — enforced: nothing spawns, `Settings → AI` shows "zero footprint" |
| toggle off, model downloaded | ~2.6 GB | 0 | none |
| toggle on, idle (after `local_idle_stop_s`) | ~2.6 GB | 0 | stopped; restarted on the next dictation |
| toggle on, running | ~2.6 GB | ~4.6 GB RSS (measured: Qwen3-4B Q4_K_M, ctx 4096, 8 threads) | one `llama-server` |

Cold start (spawn → healthy) measured at **2.5–3 s** on a 16-thread
CPU (weights are mmap'd); the first polish after a cold start takes
~8 s (prompt warm-up), subsequent polishes **~2 s**. On slower CPUs
expect the upper end of 3–15 s; the start timeout is 90 s.

Enforcement, not just documentation:

- `ai.local_polish = false` (or AI polish disabled) ⇒ the supervisor
  never spawns and the settings-change hook *stops* a running server
  immediately (`test_zero_footprint_when_off`,
  `test_apply_settings_off_stops_server`).
- `ai.local_idle_stop_s` (default 180 s, 0 = keep running) stops the
  server automatically (`test_idle_stop`).
- Daemon shutdown stops the server (`test_shutdown_stops_server`).
- Deleting the active chat model from Settings → Models is refused
  while Local polish is on; deleting the runtime is refused while the
  server runs.

## Configuration

```toml
[ai]
enabled            = true   # the master switch (both cloud and local)
local_polish       = true   # Local polish (works offline)
local_model        = "qwen3-4b-instruct-2507-q4_k_m"
local_port         = 8931   # loopback only
local_idle_stop_s  = 180    # 0 = keep the server running
```

While `local_polish` is on, `base_url`/`model`/API key are ignored for
polish (the bundled server is used instead, and the cloud key is never
sent to it); everything else — prompt profiles, per-app prompts, the
refusal guardrail, temperature — applies unchanged.

## Quality gate (local vs cloud)

The eval harness ships a polish-quality bench
([../eval/README.md](../eval/README.md)):

```bash
python -m fluidvoice.evalharness polish \
  --local \
  --cloud-base-url https://api.groq.com/openai/v1 \
  --cloud-model llama-3.3-70b-versatile \
  --cloud-api-key-env GROQ_API_KEY --require-gate
```

Both providers polish the same raw-transcript corpus
(`polish-baseline.toml`, 16 disfluent→polished cases) and are scored
against the same references; the **gate passes only when local polish
stays within the margin (default 0.05) of the cloud baseline** on
content recall and similarity, with zero hard failures (empty/refusal
outputs). A gate regression exits non-zero — wire it into your release
checks like `just gate`. Baseline evidence from this repo's machine:
[../eval/polish-baseline-2026-11-21.md](../eval/polish-baseline-2026-11-21.md).
