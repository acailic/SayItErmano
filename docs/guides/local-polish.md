# Local AI polish (works offline)

A paying user without a cloud key still gets polished text — with the
same local-only privacy promise as the transcription itself. Flip
**Settings → AI → "Local polish (works offline)"** on (or click *Use*
on a chat model under **Settings → Models → Local AI polish (llama.cpp)**),
download the model once, and every dictation is polished by a small
local chat model served by a llama.cpp server the daemon manages.
Nothing ever leaves the machine: while local polish is on, the polish
step ignores `ai.base_url` entirely.

## Pieces

| piece | what it is | where |
|---|---|---|
| chat model | `qwen3-4b-class` GGUF (~2.5 GB, Q4_K_M) | `~/.cache/sayit-ermano/models/llama.cpp/` |
| server | `llama-server` (llama.cpp) spawned by the daemon, loopback only | see discovery below |
| toggle | `ai.local_polish` (+ `ai.enabled`) | Settings → AI, or config.toml |

`llama-server` binary discovery, in order:

1. `$SAYITERMANO_LLAMA_SERVER` — explicit path to any binary;
2. `~/.cache/sayit-ermano/models/llama.cpp/bin/llama-server` — the
   managed "bundled" slot (drop a static build there yourself);
3. `llama-server` on `PATH` — a llama.cpp build you installed yourself
   (distro package if your distribution ships one, or a stock build
   from [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp);
   any recent release provides `llama-server` and its `/health`
   endpoint).

`sayit-ermano doctor` reports the toggle, the model download state,
the binary and the live server state.

## Budgets (documented and enforced)

| budget | value | enforced by |
|---|---|---|
| resting footprint, toggle OFF | zero — no process, no model RAM | nothing is ever spawned while `ai.local_polish = false`; turning it off stops a running server immediately |
| resting footprint, toggle ON, idle | zero after the idle window — the server is stopped | `ai.local_polish_idle_s` (default 300 s; 0 = keep it up with the daemon) |
| serving footprint | ≈ model file size + context overhead (~3 GB RSS for the qwen3-4b Q4_K_M default at 4 k ctx) | `-c 4096` launch flag; measure locally with `scripts/soak.py` |
| cold start | seconds from warm page cache to a minute from cold disk; hard bound 120 s (`local_ai.START_TIMEOUT_S`) | health-gated readiness: polish never talks to a half-loaded server; the measured `cold_start_s` appears in `doctor` and `status` |
| disk | ~2.5 GB for the model GGUF | managed models cache (visible + deletable in Settings → Models → Disk usage) |

## Quality gate (local vs cloud)

The eval harness ships a regression gate:
[`polish-gate`](../eval/README.md#polish-quality-gate) scores the same
raw transcripts through a local polisher and a cloud baseline against
expected polished references (WER/CER, product-faithful fallbacks) and
fails while local polish degrades output beyond the tolerance. Run it
before shipping any change to the local-polish path.

## Notes and limits

- Rewrite mode and Command mode keep using the configured
  OpenAI-compatible endpoint — local polish routes the *dictation
  polish* step only (matching the offline-polish scope of this
  feature).
- The refusal/leak/over-correction guardrails apply to local polish
  exactly as to cloud polish; a refused or failed local polish types
  the raw transcript.
- Thinking-tag output (`<think>…</think>`) from hybrid models is
  stripped by the existing client (`strip_thinking`).
